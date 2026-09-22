"""Orchestrates a single region ingest: fetch from ESI, store, prune."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import polars as pl

from eve.config import Settings
from eve.ingest.esi_client import EsiClient
from eve.store.duckdb_store import DuckDBStore
from eve.store.parquet_writer import prune_raw_snapshots, write_raw_snapshot

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    snapshot_id: str
    region_id: int
    order_count: int
    fetched_at: datetime
    expires_at: datetime
    skipped: bool


def _orders_to_dataframe(
    orders: list[dict], region_id: int, snapshot_id: str, fetched_at: datetime
) -> pl.DataFrame:
    df = pl.DataFrame(orders)
    return df.with_columns(
        pl.col("issued").str.to_datetime(time_zone="UTC"),
        pl.lit(region_id, dtype=pl.Int64).alias("region_id"),
        pl.lit(snapshot_id).alias("snapshot_id"),
        pl.lit(fetched_at).alias("fetched_at"),
    )


async def ingest_region(
    region_id: int,
    settings: Settings,
    store: DuckDBStore | None = None,
    client: EsiClient | None = None,
) -> IngestResult:
    """Fetch and store a fresh orders snapshot for `region_id`, unless still within Expires."""
    owns_store = store is None
    store = store or DuckDBStore(settings.storage.duckdb_path)
    try:
        now = datetime.now(UTC)
        existing = store.latest_snapshot_meta(region_id)
        if existing is not None and existing.expires_at > now:
            logger.info(
                "using cached snapshot %s for region %d (%d orders, expires at %s)",
                existing.snapshot_id,
                region_id,
                existing.order_count,
                existing.expires_at,
            )
            return IngestResult(
                snapshot_id=existing.snapshot_id,
                region_id=region_id,
                order_count=existing.order_count,
                fetched_at=existing.fetched_at,
                expires_at=existing.expires_at,
                skipped=True,
            )

        owns_client = client is None
        client = client or EsiClient(settings.esi)
        try:
            result = await client.fetch_region_orders(region_id)
        finally:
            if owns_client:
                await client.aclose()

        fetched_at = now
        snapshot_id = f"{region_id}-{fetched_at:%Y%m%dT%H%M%SZ}"
        orders_df = _orders_to_dataframe(result.orders, region_id, snapshot_id, fetched_at)

        parquet_path = write_raw_snapshot(
            orders_df, region_id, snapshot_id, fetched_at, settings.storage.raw_data_dir
        )
        store.write_snapshot(
            region_id, snapshot_id, fetched_at, result.expires_at, len(result.orders), parquet_path
        )
        prune_raw_snapshots(settings.storage.raw_data_dir, settings.storage.retention_days)

        logger.info(
            "ingested %d orders for region %d (snapshot %s, expires at %s)",
            len(result.orders),
            region_id,
            snapshot_id,
            result.expires_at,
        )
        return IngestResult(
            snapshot_id=snapshot_id,
            region_id=region_id,
            order_count=len(result.orders),
            fetched_at=fetched_at,
            expires_at=result.expires_at,
            skipped=False,
        )
    finally:
        if owns_store:
            store.close()
