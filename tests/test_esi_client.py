"""Tests for the async ESI client: pagination, retry, and error-limit backoff.

Uses httpx.MockTransport (no real network) and real recorded fixture pages.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from eve.config import Esi
from eve.ingest.esi_client import EsiClient

FIXTURES = Path(__file__).parent / "fixtures" / "esi"
BASE_URL = "https://esi.evetech.net/latest"


def _settings(**overrides: object) -> Esi:
    defaults: dict[str, object] = {
        "contact": "test@example.com",
        "max_retries": 3,
        "backoff_base_seconds": 0.01,
        "max_concurrency": 5,
        "error_limit_threshold": 10,
    }
    defaults.update(overrides)
    return Esi(**defaults)  # type: ignore[arg-type]


def _load_page(n: int) -> list[dict]:
    return json.loads((FIXTURES / f"region_orders_page{n}.json").read_text(encoding="utf-8"))


def _client_for(
    transport: httpx.MockTransport, settings: Esi
) -> tuple[EsiClient, httpx.AsyncClient]:
    async_client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    return EsiClient(settings, client=async_client), async_client


def test_fetch_region_orders_paginates() -> None:
    page1, page2 = _load_page(1), _load_page(2)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = int(request.url.params.get("page", "1"))
        body = page1 if page == 1 else page2
        return httpx.Response(
            200, json=body, headers={"X-Pages": "2", "Expires": "Tue, 22 Sep 2026 22:29:45 GMT"}
        )

    client, async_client = _client_for(httpx.MockTransport(handler), _settings())
    try:
        result = asyncio.run(client.fetch_region_orders(10000002))
    finally:
        asyncio.run(async_client.aclose())

    assert len(result.orders) == len(page1) + len(page2)
    assert len(calls) == 2
    assert all(c.url.params.get("order_type") == "all" for c in calls)


def test_retries_on_server_error_then_succeeds() -> None:
    page1 = _load_page(1)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200, json=page1, headers={"X-Pages": "1", "Expires": "Tue, 22 Sep 2026 22:29:45 GMT"}
        )

    client, async_client = _client_for(httpx.MockTransport(handler), _settings())
    try:
        result = asyncio.run(client.fetch_region_orders(10000002))
    finally:
        asyncio.run(async_client.aclose())

    assert attempts["n"] == 2
    assert len(result.orders) == len(page1)


def test_gives_up_after_max_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client, async_client = _client_for(httpx.MockTransport(handler), _settings(max_retries=2))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.fetch_region_orders(10000002))
    finally:
        asyncio.run(async_client.aclose())


def test_error_limit_backoff_sleeps_before_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = _load_page(1)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=page1,
            headers={
                "X-Pages": "2",
                "Expires": "Tue, 22 Sep 2026 22:29:45 GMT",
                "X-ESI-Error-Limit-Remain": "5",
                "X-ESI-Error-Limit-Reset": "12",
            },
        )

    settings = _settings(error_limit_threshold=10)
    client, async_client = _client_for(httpx.MockTransport(handler), settings)
    try:
        asyncio.run(client.fetch_region_orders(10000002))
    finally:
        asyncio.run(async_client.aclose())

    assert 12.0 in sleep_calls
