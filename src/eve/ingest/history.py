"""Daily market history ingest: once per (region, type) per day, resumable."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from eve.config import Settings
from eve.ingest.esi_client import EsiClient
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


@dataclass
class HistoryResult:
    region_id: int
    fetched: int
    skipped_already_done: int
    failed: int


async def ingest_history(
    region_id: int,
    settings: Settings,
    store: DuckDBStore | None = None,
    client: EsiClient | None = None,
) -> HistoryResult:
    """Fetch daily history for every type currently traded in `region_id`, once per day.

    Already-attempted (region, type, today) pairs are skipped -- safe to re-run after
    an interruption, it just picks up where it left off.
    """
    owns_store = store is None
    store = store or DuckDBStore(settings.storage.duckdb_path)
    try:
        today = datetime.now(UTC).date()
        total_types = store.count_order_types(region_id)
        pending = sorted(store.pending_history_types(region_id, today))
        skipped_already_done = total_types - len(pending)

        owns_client = client is None
        client = client or EsiClient(settings.esi)
        fetched = 0
        failed = 0
        try:
            semaphore = asyncio.Semaphore(settings.esi.max_concurrency)

            async def fetch_one(type_id: int) -> None:
                nonlocal fetched, failed
                async with semaphore:
                    try:
                        rows = await client.fetch_history(region_id, type_id)
                    except Exception:
                        logger.exception(
                            "failed to fetch history for region %d type %d", region_id, type_id
                        )
                        failed += 1
                        return
                store.write_history(region_id, type_id, today, rows)
                fetched += 1

            await asyncio.gather(*(fetch_one(t) for t in pending))
        finally:
            if owns_client:
                await client.aclose()

        logger.info(
            "history ingest for region %d: %d fetched, %d failed (%d already done today)",
            region_id,
            fetched,
            failed,
            skipped_already_done,
        )
        return HistoryResult(
            region_id=region_id,
            fetched=fetched,
            skipped_already_done=skipped_already_done,
            failed=failed,
        )
    finally:
        if owns_store:
            store.close()
