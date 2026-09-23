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


def test_search_types_by_name_matches_case_insensitive_and_published_only(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        store._con.execute(
            "INSERT INTO types VALUES (?, ?, ?, ?, ?)", [34, "Tritanium", 0.01, 18, True]
        )
        store._con.execute(
            "INSERT INTO types VALUES (?, ?, ?, ?, ?)", [35, "Pyerite", 0.01, 18, True]
        )
        store._con.execute(
            "INSERT INTO types VALUES (?, ?, ?, ?, ?)",
            [36, "Unpublished Tritanium Ore", 0.01, 18, False],
        )

        results = store.search_types_by_name("tri")

    assert results == [(34, "Tritanium")]


def test_latest_book_features_for_type_filters_to_one_type(tmp_path: Path) -> None:
    fetched_at = datetime.now(UTC)
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        store._con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            [
                "snap-1",
                10000002,
                fetched_at.replace(tzinfo=None),
                (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
                2,
            ],
        )
        for type_id, best_bid in ((34, 5.0), (35, 9.0)):
            store._con.execute(
                """
                INSERT INTO book_features
                (snapshot_id, location_id, type_id, best_bid, best_ask, bid_depth_volumes,
                 ask_depth_volumes, bid_orders_near_top, ask_orders_near_top, history_avg_7d,
                 history_avg_30d, price_deviation_30d_pct, computed_at)
                VALUES ('snap-1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    60003760,
                    type_id,
                    best_bid,
                    best_bid + 1,
                    [],
                    [],
                    2,
                    2,
                    best_bid,
                    best_bid,
                    0.0,
                    fetched_at.replace(tzinfo=None),
                ],
            )

        row = store.latest_book_features_for_type(10000002, 60003760, 34)
        missing = store.latest_book_features_for_type(10000002, 60003760, 999)

    assert row[0] == 34
    assert row[1] == 5.0
    assert missing is None


def test_list_plan_runs_orders_newest_first_and_filters_by_run_type(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        store.write_plan_run(
            "run-1", "mm", now - timedelta(minutes=10), 60003760, None, 100.0, "per_day"
        )
        store.write_plan_run("run-2", "haul", now, 60003760, 60008494, 200.0, "per_run")

        all_runs = store.list_plan_runs()
        mm_runs = store.list_plan_runs(run_type="mm")

    assert [r[0] for r in all_runs] == ["run-2", "run-1"]
    assert [r[0] for r in mm_runs] == ["run-1"]
