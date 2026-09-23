"""`eve derive`: incremental book-features + snapshot-diff refresh for cron."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_fill_model import LOCATION_ID, REGION_ID, TYPE_ID, _order, _write_snapshot
from test_fill_model import _settings as _fill_settings

from eve.config import StationPair
from eve.fill.fill_model import run_diff_for_region
from eve.ops import derive as derive_module
from eve.ops.derive import run_derive_all
from eve.store.duckdb_store import DuckDBStore

SYSTEM_ID = 30000142


def _settings(tmp_path: Path):
    settings = _fill_settings(tmp_path)
    settings.station_pairs = [StationPair(from_station_id=LOCATION_ID, to_station_id=LOCATION_ID)]
    return settings


def _seed(store: DuckDBStore, settings, now: datetime) -> None:
    store._con.execute(
        "INSERT INTO solar_systems VALUES (?, ?, 1, 0.9, 'Jita')", [SYSTEM_ID, REGION_ID]
    )
    store.write_location(LOCATION_ID, "station", "Jita IV", SYSTEM_ID)
    raw = Path(settings.storage.raw_data_dir)
    orders = [_order(1, 5.0, 100), _order(2, 6.0, 50, is_buy=False)]
    later = [_order(1, 5.0, 90), _order(2, 6.0, 50, is_buy=False)]  # order 1 partially filled
    _write_snapshot(store, raw, "snap-1", now - timedelta(minutes=10), orders)
    _write_snapshot(store, raw, "snap-2", now - timedelta(minutes=1), later)
    # `_write_snapshot` only writes parquet + the snapshots row; seed the live book directly.
    for oid, o in enumerate(later, start=1):
        store._con.execute(
            """
            INSERT INTO orders_latest
            (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
             volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, 1000, ?, 1, 90, 'station', ?, 'snap-2', ?)
            """,
            [
                oid,
                TYPE_ID,
                LOCATION_ID,
                SYSTEM_ID,
                o["is_buy_order"],
                o["price"],
                o["volume_remain"],
                REGION_ID,
                (now - timedelta(minutes=1)).replace(tzinfo=None),
            ],
        )


def test_first_run_derives_then_second_run_is_a_noop(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed(store, settings, now)

        (first,) = run_derive_all(store, settings)
        (second,) = run_derive_all(store, settings)

        assert first.success and first.feature_rows > 0 and first.pairs_diffed == 1
        assert second.success and second.feature_rows == 0 and second.pairs_diffed == 0
        assert store.has_book_features("snap-2")
        runs = store._con.execute(
            "SELECT command, success FROM ingest_runs WHERE command = 'derive'"
        ).fetchall()
        assert runs == [("derive", True), ("derive", True)]


def test_failure_is_recorded_and_visible_as_an_ingest_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)

    def boom(*args, **kwargs):
        raise RuntimeError("features exploded")

    monkeypatch.setattr(derive_module, "compute_book_features", boom)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed(store, settings, now)

        (outcome,) = run_derive_all(store, settings)

        assert not outcome.success and "exploded" in outcome.error_message
        (row,) = store.recent_ingest_runs(now - timedelta(hours=1))
        assert (row[1], row[5], row[7]) == ("derive", False, "features exploded")


def test_old_book_features_are_pruned(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed(store, settings, now)
        old = (now - timedelta(days=settings.features.retention_days + 1)).replace(tzinfo=None)
        store._con.execute(
            "INSERT INTO book_features VALUES ('snap-old', ?, ?, 1, 2, [], [], 1, 1, 1, 1, 0, ?)",
            [LOCATION_ID, TYPE_ID, old],
        )

        run_derive_all(store, settings)

        assert not store.has_book_features("snap-old")
        assert store.has_book_features("snap-2")


def test_diff_skip_existing_and_since_only_touch_new_pairs(tmp_path: Path) -> None:
    settings = _fill_settings(tmp_path)
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        raw = Path(settings.storage.raw_data_dir)
        for i in range(4):
            _write_snapshot(
                store, raw, f"snap-{i}", t0 + timedelta(minutes=10 * i), [_order(1, 5.0, 100 - i)]
            )

        first = run_diff_for_region(store, REGION_ID, settings, skip_existing=True)
        again = run_diff_for_region(store, REGION_ID, settings, skip_existing=True)
        recent = run_diff_for_region(
            store, REGION_ID, settings, since=t0 + timedelta(minutes=25)
        )  # only the pair ending at +30 min

    assert (first.pairs_processed, again.pairs_processed, recent.pairs_processed) == (3, 0, 1)
