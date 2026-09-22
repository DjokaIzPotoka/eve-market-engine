"""Tests for fill_model: capture rate, end-to-end diff run, idempotency, calibration.

Synthetic 3-snapshot scenario written directly as Parquet + seeded `snapshots`/`history`
rows -- purely derived data, no external API shape involved, so no "real fixtures" needed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Features,
    Fill,
    Filters,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.fill.fill_model import calibration_check, estimate_capture_rate, run_diff_for_region
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 10000002
LOCATION_ID = 60003760
TYPE_ID = 34

_SCHEMA = {
    "order_id": pl.Int64,
    "region_id": pl.Int64,
    "type_id": pl.Int64,
    "location_id": pl.Int64,
    "is_buy_order": pl.Boolean,
    "price": pl.Float64,
    "volume_remain": pl.Int64,
}


def _order(order_id: int, price: float, volume_remain: int, is_buy: bool = True) -> dict:
    return {
        "order_id": order_id,
        "region_id": REGION_ID,
        "type_id": TYPE_ID,
        "location_id": LOCATION_ID,
        "is_buy_order": is_buy,
        "price": price,
        "volume_remain": volume_remain,
    }


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=1, to_station_id=2)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(
            duckdb_path=str(tmp_path / "eve.duckdb"), raw_data_dir=str(tmp_path / "raw")
        ),
        features=Features(),
        fill=Fill(),
    )


def _write_snapshot(
    store: DuckDBStore,
    raw_data_dir: Path,
    snapshot_id: str,
    fetched_at: datetime,
    orders: list[dict],
) -> None:
    raw_dir = raw_data_dir / f"region={REGION_ID}" / f"date={fetched_at:%Y-%m-%d}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(orders, schema=_SCHEMA) if orders else pl.DataFrame(schema=_SCHEMA)
    df.write_parquet(raw_dir / f"{snapshot_id}.parquet")
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        [
            snapshot_id,
            REGION_ID,
            fetched_at.replace(tzinfo=None),
            (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
            len(orders),
        ],
    )


def test_estimate_capture_rate_decreases_with_competition() -> None:
    assert estimate_capture_rate(0) == 1.0
    assert estimate_capture_rate(1) == 0.5
    assert estimate_capture_rate(4) == 0.2
    assert estimate_capture_rate(9) < estimate_capture_rate(4)


def test_run_diff_for_region_end_to_end(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    # order A: buy, fills partially then fully; order B: sell, relists then fills;
    # order C: buy, sits unchanged the whole time (a competitor for A)
    snap1 = [_order(1, 5.0, 100), _order(2, 6.0, 50, is_buy=False), _order(3, 4.9, 20)]
    snap2 = [_order(1, 5.0, 60), _order(2, 6.5, 50, is_buy=False), _order(3, 4.9, 20)]
    snap3 = [_order(2, 6.5, 30, is_buy=False), _order(3, 4.9, 20)]  # order 1 disappeared

    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_history(
            REGION_ID,
            TYPE_ID,
            date(2026, 9, 20),
            [
                {
                    "date": "2026-09-20",
                    "average": 5.5,
                    "highest": 6.5,
                    "lowest": 5.0,
                    "order_count": 10,
                    # generous enough that 60 units disappearing in 10 min is plausible
                    # (plausible capacity = 20000 * (10/60/24) ~= 139 units)
                    "volume": 20000,
                }
            ],
        )
        raw_dir = Path(settings.storage.raw_data_dir)
        _write_snapshot(store, raw_dir, "snap-1", t0, snap1)
        _write_snapshot(store, raw_dir, "snap-2", t0 + timedelta(minutes=10), snap2)
        _write_snapshot(store, raw_dir, "snap-3", t0 + timedelta(minutes=20), snap3)

        result = run_diff_for_region(store, REGION_ID, settings)

        assert result.pairs_processed == 2
        assert result.pairs_skipped_missing_file == 0
        # order 1: 40 (pair 1) + 60 (pair 2, disappearance); order 2: 20 (pair 2)
        assert result.event_counts["fill"] == 3
        assert result.event_counts["relist"] == 1  # order 2's price change in pair 1

        # order 1's disappearance between snap-2 and snap-3: volume 60 is plausible given history
        events = store._con.execute(
            "SELECT event_type, filled_volume FROM snapshot_diff_events WHERE order_id = 1 "
            "AND from_snapshot_id = 'snap-2'"
        ).fetchall()
        assert events == [("fill", 60)]

        metrics = store._con.execute(
            "SELECT bid_competitor_count, estimated_bid_capture_rate "
            "FROM book_diff_metrics WHERE location_id = ? AND type_id = ? "
            "AND from_snapshot_id = 'snap-1'",
            [LOCATION_ID, TYPE_ID],
        ).fetchone()
        # at snap-2, orders 1 and 3 are both still within near_top_pct of the best bid (5.0)
        assert metrics[0] == 2
        assert metrics[1] == estimate_capture_rate(2)


def test_run_diff_for_region_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    snap1 = [_order(1, 5.0, 100)]
    snap2 = [_order(1, 5.0, 60)]

    with DuckDBStore(settings.storage.duckdb_path) as store:
        raw_dir = Path(settings.storage.raw_data_dir)
        _write_snapshot(store, raw_dir, "snap-1", t0, snap1)
        _write_snapshot(store, raw_dir, "snap-2", t0 + timedelta(minutes=10), snap2)

        run_diff_for_region(store, REGION_ID, settings)
        first_count = store._con.execute("SELECT COUNT(*) FROM snapshot_diff_events").fetchone()[0]

        run_diff_for_region(store, REGION_ID, settings)
        second_count = store._con.execute("SELECT COUNT(*) FROM snapshot_diff_events").fetchone()[0]

    assert first_count > 0
    assert second_count == first_count


def test_run_diff_for_region_skips_pairs_with_missing_raw_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        raw_dir = Path(settings.storage.raw_data_dir)
        _write_snapshot(store, raw_dir, "snap-1", t0, [_order(1, 5.0, 100)])
        # snap-2's metadata exists but its raw parquet was "pruned" -- never written
        store._con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            [
                "snap-2",
                REGION_ID,
                (t0 + timedelta(minutes=10)).replace(tzinfo=None),
                (t0 + timedelta(minutes=15)).replace(tzinfo=None),
                1,
            ],
        )

        result = run_diff_for_region(store, REGION_ID, settings)

    assert result.pairs_processed == 0
    assert result.pairs_skipped_missing_file == 1


def test_calibration_check_compares_estimated_to_actual(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    snap1 = [_order(1, 5.0, 100)]
    snap2 = [_order(1, 5.0, 40)]  # 60 units filled

    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_history(
            REGION_ID,
            TYPE_ID,
            date(2026, 9, 20),
            [
                {
                    "date": "2026-09-20",
                    "average": 5.0,
                    "highest": 5.0,
                    "lowest": 5.0,
                    "order_count": 5,
                    "volume": 200,
                }
            ],
        )
        raw_dir = Path(settings.storage.raw_data_dir)
        _write_snapshot(store, raw_dir, "snap-1", t0, snap1)
        _write_snapshot(store, raw_dir, "snap-2", t0 + timedelta(minutes=10), snap2)
        run_diff_for_region(store, REGION_ID, settings)

        result = calibration_check(store, REGION_ID, TYPE_ID, date(2026, 9, 20))

    assert result.estimated_volume == 60
    assert result.actual_volume == 200
    assert result.ratio == 60 / 200
