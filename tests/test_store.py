"""Tests for DuckDBStore: snapshot metadata and orders_latest replacement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from eve.store.duckdb_store import DuckDBStore


def _write_orders_parquet(
    path: Path, order_ids: list[int], region_id: int, snapshot_id: str, fetched_at: datetime
) -> Path:
    n = len(order_ids)
    df = pl.DataFrame(
        {
            "order_id": order_ids,
            "type_id": [34] * n,
            "location_id": [60003760] * n,
            "system_id": [30000142] * n,
            "is_buy_order": [False] * n,
            "price": [5.0] * n,
            "volume_total": [100] * n,
            "volume_remain": [100] * n,
            "min_volume": [1] * n,
            "duration": [90] * n,
            "issued": [datetime(2026, 9, 20, tzinfo=UTC)] * n,
            "range": ["station"] * n,
            "region_id": [region_id] * n,
            "snapshot_id": [snapshot_id] * n,
            "fetched_at": [fetched_at] * n,
        }
    )
    df.write_parquet(path)
    return path


def test_write_snapshot_and_read_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "eve.duckdb"
    fetched_at = datetime.now(UTC)
    expires_at = fetched_at + timedelta(minutes=5)
    parquet_path = _write_orders_parquet(
        tmp_path / "snap-1.parquet", [1, 2, 3], 10000002, "snap-1", fetched_at
    )

    with DuckDBStore(db_path) as store:
        assert store.latest_snapshot_meta(10000002) is None

        store.write_snapshot(10000002, "snap-1", fetched_at, expires_at, 3, parquet_path)

        meta = store.latest_snapshot_meta(10000002)
        assert meta is not None
        assert meta.snapshot_id == "snap-1"
        assert meta.order_count == 3
        assert meta.expires_at == expires_at


def test_write_snapshot_replaces_orders_latest_for_region(tmp_path: Path) -> None:
    db_path = tmp_path / "eve.duckdb"
    fetched_at_1 = datetime.now(UTC)
    fetched_at_2 = fetched_at_1 + timedelta(minutes=1)
    expires_at = fetched_at_2 + timedelta(minutes=5)

    with DuckDBStore(db_path) as store:
        parquet_1 = _write_orders_parquet(
            tmp_path / "snap-1.parquet", [1, 2], 10000002, "snap-1", fetched_at_1
        )
        store.write_snapshot(10000002, "snap-1", fetched_at_1, expires_at, 2, parquet_1)

        parquet_2 = _write_orders_parquet(
            tmp_path / "snap-2.parquet", [10, 11, 12], 10000002, "snap-2", fetched_at_2
        )
        store.write_snapshot(10000002, "snap-2", fetched_at_2, expires_at, 3, parquet_2)

        rows = store._con.execute(
            "SELECT order_id FROM orders_latest WHERE region_id = ? ORDER BY order_id",
            [10000002],
        ).fetchall()
        assert [r[0] for r in rows] == [10, 11, 12]

        meta = store.latest_snapshot_meta(10000002)
        assert meta is not None
        assert meta.snapshot_id == "snap-2"


def test_write_snapshot_keeps_other_regions_untouched(tmp_path: Path) -> None:
    db_path = tmp_path / "eve.duckdb"
    fetched_at = datetime.now(UTC)
    expires_at = fetched_at + timedelta(minutes=5)

    with DuckDBStore(db_path) as store:
        forge_parquet = _write_orders_parquet(
            tmp_path / "forge.parquet", [1], 10000002, "forge-1", fetched_at
        )
        store.write_snapshot(10000002, "forge-1", fetched_at, expires_at, 1, forge_parquet)

        domain_parquet = _write_orders_parquet(
            tmp_path / "domain.parquet", [2], 10000043, "domain-1", fetched_at
        )
        store.write_snapshot(10000043, "domain-1", fetched_at, expires_at, 1, domain_parquet)

        forge_rows = store._con.execute(
            "SELECT order_id FROM orders_latest WHERE region_id = ?", [10000002]
        ).fetchall()
        assert [r[0] for r in forge_rows] == [1]
