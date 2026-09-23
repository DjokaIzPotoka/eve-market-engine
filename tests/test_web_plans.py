"""Tests for the plan-run listing/detail pages and the mm/haul trigger forms."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Features,
    Fees,
    Fill,
    Filters,
    Hauling,
    Health,
    MarketMaking,
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
SYS_A, SYS_B = 30000142, 30002187
STATION_A, STATION_B = 60003760, 60008494
FETCHED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=500_000_000),
        cargo=Cargo(default_m3=5000),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_B)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path=str(tmp_path / "eve.duckdb")),
        features=Features(),
        fill=Fill(),
        fees=Fees(),
        hauling=Hauling(),
        market_making=MarketMaking(),
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
        "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_B, REGION_ID, 1, 0.9, "Amarr"]
    )
    store._con.execute(
        "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "Jita", SYS_A]
    )
    store._con.execute(
        "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_B, "Amarr", SYS_B]
    )
    store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [SYS_A, SYS_B])
    store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [SYS_B, SYS_A])


def _seed_mm_candidate(store: DuckDBStore) -> None:
    store._con.execute("INSERT INTO types VALUES (34, 'Tritanium', 0.01, 18, true)")
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        ["snap-1", REGION_ID, FETCHED_AT.replace(tzinfo=None), FETCHED_AT.replace(tzinfo=None), 1],
    )
    store._con.execute(
        """
        INSERT INTO book_features
        (snapshot_id, location_id, type_id, best_bid, best_ask, bid_depth_volumes,
         ask_depth_volumes, bid_orders_near_top, ask_orders_near_top, history_avg_7d,
         history_avg_30d, price_deviation_30d_pct, computed_at)
        VALUES ('snap-1', ?, 34, 1000.0, 1600.0, ?, ?, 3, 3, 1300.0, 1300.0, 0.0, ?)
        """,
        [STATION_A, [], [], FETCHED_AT.replace(tzinfo=None)],
    )
    store._con.execute(
        """
        INSERT INTO book_diff_metrics
        (region_id, location_id, type_id, from_snapshot_id, to_snapshot_id, from_fetched_at,
         to_fetched_at, elapsed_hours, bid_volume_per_hour, ask_volume_per_hour,
         relists_per_hour, bid_competitor_count, ask_competitor_count,
         estimated_bid_capture_rate, estimated_ask_capture_rate)
        VALUES (?, ?, 34, 'from-1', 'to-1', ?, ?, 1.0, 2.0, 2.0, 0.02, 1, 1, 0.5, 0.5)
        """,
        [REGION_ID, STATION_A, FETCHED_AT.replace(tzinfo=None), FETCHED_AT.replace(tzinfo=None)],
    )


def _seed_haul_orders(store: DuckDBStore) -> None:
    store._con.execute("INSERT INTO types VALUES (34, 'Tritanium', 0.01, 18, true)")
    store._con.execute(
        """
        INSERT INTO orders_latest
        (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
         volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
        VALUES (1, 34, ?, ?, false, 5.0, 1000000, 1000000, 1, 90, 'station', ?, 'snap-1', ?)
        """,
        [STATION_A, SYS_A, REGION_ID, FETCHED_AT.replace(tzinfo=None)],
    )
    store._con.execute(
        """
        INSERT INTO orders_latest
        (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
         volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
        VALUES (2, 34, ?, ?, true, 6.5, 500000, 500000, 1, 90, 'station', ?, 'snap-1', ?)
        """,
        [STATION_B, SYS_B, REGION_ID, FETCHED_AT.replace(tzinfo=None)],
    )


def test_list_plans_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).get("/plans")

    assert response.status_code == 200
    assert "No saved plans yet" in response.text


def test_plan_detail_404_for_unknown_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).get("/plans/does-not-exist")

    assert response.status_code == 404


def test_plan_detail_shows_seeded_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_plan_run("run-1", "mm", datetime.now(UTC), STATION_A, None, 500.0, "per_day")
        store.write_plan_lines(
            "run-1",
            [
                {
                    "type_id": 34,
                    "type_name": "Tritanium",
                    "suggested_quantity": 100.0,
                    "suggested_buy_price": 5.0,
                    "suggested_sell_price": 8.0,
                }
            ],
        )

    response = _client(settings).get("/plans/run-1")

    assert response.status_code == 200
    assert "Tritanium" in response.text


def test_mm_form_renders(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).get("/mm")

    assert response.status_code == 200
    assert "Market Making" in response.text


def test_mm_submit_saves_plan_and_shows_result(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_mm_candidate(store)

        response = _client(settings).post(
            "/mm", data={"station_id": STATION_A, "capital": 500_000_000}
        )

        runs = store._con.execute("SELECT run_type FROM plan_runs").fetchall()

    assert response.status_code == 200
    assert "Tritanium" in response.text
    assert runs == [("mm",)]


def test_haul_form_renders(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).get("/haul")

    assert response.status_code == 200
    assert "Hauling" in response.text


def test_haul_submit_single_pair_saves_plan(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_haul_orders(store)

        response = _client(settings).post(
            "/haul",
            data={
                "from_station_id": STATION_A,
                "to_station_id": STATION_B,
                "budget": 20_000_000,
                "cargo_m3": 5000,
            },
        )

        runs = store._con.execute("SELECT run_type FROM plan_runs").fetchall()

    assert response.status_code == 200
    assert "Tritanium" in response.text
    assert runs == [("haul",)]


def test_haul_submit_all_pairs_saves_one_plan_per_pair(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_haul_orders(store)

        response = _client(settings).post(
            "/haul",
            data={
                "from_station_id": STATION_A,
                "to_station_id": STATION_B,
                "budget": 20_000_000,
                "cargo_m3": 5000,
                "all_pairs": "true",
            },
        )

        runs = store._con.execute("SELECT run_type FROM plan_runs").fetchall()

    assert response.status_code == 200
    assert runs == [("haul",)]  # one configured station pair


def test_haul_submit_unknown_station_returns_400_not_500(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)

    response = _client(settings).post(
        "/haul",
        data={
            "from_station_id": 999999,
            "to_station_id": STATION_B,
            "budget": 1000,
            "cargo_m3": 100,
        },
    )

    assert response.status_code == 400
    assert "unknown station" in response.text
