"""Tests for the web dashboard's GET / health page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

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
from eve.store.duckdb_store import DuckDBStore
from eve.web.app import create_app
from eve.web.deps import get_settings

REGION_ID = 10000002
SYS_A = 30000142
STATION_A = 60003760


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_A)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path=str(tmp_path / "eve.duckdb")),
        health=Health(),
    )


def _client(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _seed_topology(store: DuckDBStore) -> None:
    store._con.execute(
        "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_A, REGION_ID, 1, 0.9, "Jita"]
    )
    store._con.execute(
        "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "Jita", SYS_A]
    )


def test_home_shows_healthy_status(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        fetched_at = datetime.now(UTC) - timedelta(minutes=2)
        store._con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            [
                "snap-1",
                REGION_ID,
                fetched_at.replace(tzinfo=None),
                (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
                5,
            ],
        )

    response = _client(settings).get("/")

    assert response.status_code == 200
    assert "Healthy" in response.text
    assert "STALE" not in response.text


def test_home_shows_stale_status(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        old = datetime.now(UTC) - timedelta(hours=2)
        store._con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            [
                "snap-1",
                REGION_ID,
                old.replace(tzinfo=None),
                (old + timedelta(minutes=5)).replace(tzinfo=None),
                5,
            ],
        )

    response = _client(settings).get("/")

    assert response.status_code == 200
    assert "UNHEALTHY" in response.text
    assert "STALE" in response.text
