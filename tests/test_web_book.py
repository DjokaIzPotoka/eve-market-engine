"""Tests for the item search and per-item order-book pages."""

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
TYPE_ID = 34


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


def test_search_finds_matching_item_and_links_to_detail(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store._con.execute(
            "INSERT INTO types VALUES (?, ?, ?, ?, ?)", [TYPE_ID, "Tritanium", 0.01, 18, True]
        )

    response = _client(settings).get(f"/book?station_id={STATION_A}&q=trit")

    assert response.status_code == 200
    assert f"/book/{STATION_A}/{TYPE_ID}" in response.text


def test_book_detail_shows_best_bid_ask(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fetched_at = datetime.now(UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store._con.execute(
            "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_A, REGION_ID, 1, 0.9, "Jita"]
        )
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "Jita", SYS_A]
        )
        store._con.execute(
            "INSERT INTO types VALUES (?, ?, ?, ?, ?)", [TYPE_ID, "Tritanium", 0.01, 18, True]
        )
        store._con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            [
                "snap-1",
                REGION_ID,
                fetched_at.replace(tzinfo=None),
                (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
                1,
            ],
        )
        store._con.execute(
            """
            INSERT INTO book_features
            (snapshot_id, location_id, type_id, best_bid, best_ask, bid_depth_volumes,
             ask_depth_volumes, bid_orders_near_top, ask_orders_near_top, history_avg_7d,
             history_avg_30d, price_deviation_30d_pct, computed_at)
            VALUES ('snap-1', ?, ?, 4.8, 5.0, ?, ?, 3, 3, 4.9, 4.9, 0.0, ?)
            """,
            [STATION_A, TYPE_ID, [], [], fetched_at.replace(tzinfo=None)],
        )

    response = _client(settings).get(f"/book/{STATION_A}/{TYPE_ID}")

    assert response.status_code == 200
    assert "4.80" in response.text
    assert "5.00" in response.text


def test_book_detail_404_for_unknown_station(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).get(f"/book/999999/{TYPE_ID}")

    assert response.status_code == 404


def test_book_detail_404_when_no_snapshot_yet(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store._con.execute(
            "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_A, REGION_ID, 1, 0.9, "Jita"]
        )
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "Jita", SYS_A]
        )

    response = _client(settings).get(f"/book/{STATION_A}/{TYPE_ID}")

    assert response.status_code == 404
