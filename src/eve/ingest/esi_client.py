"""Async ESI client: retries, error-limit backoff, and paginated region-order fetches."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from eve.config import Esi

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {420, 429, 500, 502, 503, 504}


@dataclass
class RegionOrdersResult:
    orders: list[dict]
    expires_at: datetime


class EsiClient:
    """Thin async wrapper around httpx for ESI: User-Agent, retry/backoff, error-limit throttle."""

    def __init__(self, settings: Esi, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            headers={"User-Agent": f"{settings.app_name}/0.1 (contact: {settings.contact})"},
            timeout=30.0,
        )
        self._error_limit_lock = asyncio.Lock()
        self._error_limit_remain: int | None = None
        self._error_limit_reset: float | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> EsiClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _respect_error_limit(self) -> None:
        async with self._error_limit_lock:
            remain = self._error_limit_remain
            reset = self._error_limit_reset
            if remain is None or reset is None or remain > self._settings.error_limit_threshold:
                return
            # Clear first so a concurrent caller queued behind the lock doesn't sleep again.
            self._error_limit_remain = None
            self._error_limit_reset = None
            logger.warning("ESI error limit low (%d remain), sleeping %.1fs", remain, reset)
            await asyncio.sleep(reset)

    def _update_error_limit(self, response: httpx.Response) -> None:
        remain = response.headers.get("x-esi-error-limit-remain")
        reset = response.headers.get("x-esi-error-limit-reset")
        if remain is not None:
            self._error_limit_remain = int(remain)
        if reset is not None:
            self._error_limit_reset = float(reset)

    async def _sleep_backoff(self, attempt: int) -> None:
        base = self._settings.backoff_base_seconds
        delay = base * (2 ** (attempt - 1)) + random.uniform(0, base)
        logger.info("retrying ESI request (attempt %d) in %.1fs", attempt, delay)
        await asyncio.sleep(delay)

    async def _request(
        self, method: str, url: str, *, params: dict | None = None
    ) -> httpx.Response:
        attempt = 0
        while True:
            await self._respect_error_limit()
            try:
                response = await self._client.request(method, url, params=params)
            except httpx.TransportError:
                attempt += 1
                if attempt > self._settings.max_retries:
                    raise
                await self._sleep_backoff(attempt)
                continue

            self._update_error_limit(response)

            if response.status_code in RETRYABLE_STATUS_CODES:
                attempt += 1
                if attempt > self._settings.max_retries:
                    response.raise_for_status()
                await self._sleep_backoff(attempt)
                continue

            response.raise_for_status()
            return response

    @staticmethod
    def _parse_expires(response: httpx.Response) -> datetime:
        raw = response.headers.get("expires")
        if raw is None:
            return datetime.now(UTC)
        return parsedate_to_datetime(raw)

    async def fetch_region_orders(self, region_id: int) -> RegionOrdersResult:
        """Fetch every page of `/markets/{region_id}/orders/?order_type=all`."""
        path = f"/markets/{region_id}/orders/"
        first = await self._request("GET", path, params={"order_type": "all", "page": 1})
        orders: list[dict] = list(first.json())
        total_pages = int(first.headers.get("x-pages", "1"))
        expires_values = [self._parse_expires(first)]

        if total_pages > 1:
            semaphore = asyncio.Semaphore(self._settings.max_concurrency)

            async def fetch_page(page: int) -> httpx.Response:
                async with semaphore:
                    params = {"order_type": "all", "page": page}
                    return await self._request("GET", path, params=params)

            responses = await asyncio.gather(*(fetch_page(p) for p in range(2, total_pages + 1)))
            for response in responses:
                orders.extend(response.json())
                expires_values.append(self._parse_expires(response))

        return RegionOrdersResult(orders=orders, expires_at=min(expires_values))
