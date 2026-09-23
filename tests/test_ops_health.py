"""Tests for check_health: staleness detection and recent-error surfacing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Filters,
    Health,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.ops.health import check_health
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 10000002
SYS_A = 30000142
STATION_A = 60003760


def _settings(tmp_path: Path, **health_overrides: object) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_A)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path=str(tmp_path / "eve.duckdb")),
        health=Health(**health_overrides),
    )


def _seed_topology(store: DuckDBStore) -> None:
    store._con.execute(
        "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_A, REGION_ID, 1, 0.9, "Jita"]
    )
    store._con.execute(
        "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "Jita", SYS_A]
    )


def _seed_snapshot(store: DuckDBStore, fetched_at: datetime, order_count: int = 42) -> None:
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        [
            "snap-1",
            REGION_ID,
            fetched_at.replace(tzinfo=None),
            (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
            order_count,
        ],
    )


def test_fresh_snapshot_is_not_stale(tmp_path: Path) -> None:
    settings = _settings(tmp_path, stale_after_minutes=20.0)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, datetime.now(UTC) - timedelta(minutes=5))

        report = check_health(store, settings)

    assert len(report.regions) == 1
    assert report.regions[0].stale is False
    assert report.regions[0].order_count == 42
    assert report.healthy is True


def test_old_snapshot_is_stale(tmp_path: Path) -> None:
    settings = _settings(tmp_path, stale_after_minutes=20.0)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, datetime.now(UTC) - timedelta(minutes=45))

        report = check_health(store, settings)

    assert report.regions[0].stale is True
    assert report.healthy is False


def test_no_snapshot_at_all_is_stale(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)

        report = check_health(store, settings)

    assert report.regions[0].last_snapshot_id is None
    assert report.regions[0].stale is True
    assert report.healthy is False


def test_recent_error_is_reported_and_old_one_is_not(tmp_path: Path) -> None:
    settings = _settings(tmp_path, error_lookback_hours=24.0)
    now = datetime.now(UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, now - timedelta(minutes=5))

        store.write_ingest_run(
            "run-recent", "ingest", REGION_ID, now, now, False, error_message="recent boom"
        )
        old = now - timedelta(hours=48)
        store.write_ingest_run(
            "run-old", "ingest", REGION_ID, old, old, False, error_message="old boom"
        )

        report = check_health(store, settings)

    messages = [e.error_message for e in report.recent_errors]
    assert messages == ["recent boom"]
    assert report.healthy is False
