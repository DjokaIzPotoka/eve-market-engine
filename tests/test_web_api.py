"""JSON API for the React dashboard: status, stations, haul, market-making, trades, settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_web_plans import (
    REGION_ID,
    STATION_A,
    STATION_B,
    _client,
    _seed_haul_orders,
    _seed_mm_candidate,
    _seed_topology,
    _settings,
)

from eve.store.duckdb_store import DuckDBStore


def _haul_body(**over: object) -> dict:
    return {
        "from_station_id": STATION_A,
        "to_station_id": STATION_B,
        "budget_isk": 20_000_000,
        "cargo_m3": 5000,
        **over,
    }


def test_status_reports_region_snapshot_and_recent_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        store.write_location(REGION_ID, kind="region", name="The Forge")
        fetched = (now - timedelta(minutes=3)).replace(tzinfo=None)
        store._con.execute(
            "INSERT INTO snapshots VALUES ('snap-1', ?, ?, ?, 412883)",
            [REGION_ID, fetched, fetched + timedelta(minutes=5)],
        )
        store.write_ingest_run("r-ok", "ingest", REGION_ID, now, now, True, order_count=412883)
        store.write_ingest_run(
            "r-bad", "ingest", REGION_ID, now, now, False, error_message="ESI 504"
        )

    body = _client(settings).get("/api/status").json()

    (region,) = body["regions"]
    assert region["region_name"] == "The Forge"
    assert region["stale"] is False
    assert region["last_snapshot"]["order_count"] == 412883
    assert region["last_snapshot"]["fetched_at"].endswith("Z")
    assert [e["error_message"] for e in body["recent_errors"]] == ["ESI 504"]
    assert (body["runs_24h_ok"], body["runs_24h_failed"]) == (1, 1)
    # No telemetry recorded yet: explicit null/empty, never invented values.
    assert body["esi_limit"] is None
    assert body["disk"] == []


def test_status_includes_latest_esi_limit_and_disk_samples(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_esi_limit_sample(now - timedelta(minutes=10), 80, 30.0)
        store.write_esi_limit_sample(now - timedelta(minutes=1), 97, 42.0)
        store.write_disk_sample(now - timedelta(days=20), 1, 10)  # outside the 14-day chart
        store.write_disk_sample(now - timedelta(days=1), 40, 100)
        store.write_disk_sample(now, 41, 100)

    body = _client(settings).get("/api/status").json()

    assert (body["esi_limit"]["remain"], body["esi_limit"]["reset_s"]) == (97, 42.0)
    assert [d["used_bytes"] for d in body["disk"]] == [40, 41]


def test_stations_include_region_id(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)

    body = _client(settings).get("/api/stations").json()

    assert {s["location_id"] for s in body} == {STATION_A, STATION_B}
    assert all(s["region_id"] == REGION_ID for s in body)


def test_haul_returns_plan_saves_run_and_respects_limits(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_haul_orders(store)

    response = _client(settings).post("/api/haul", json=_haul_body(exit_mode="instant"))

    assert response.status_code == 200
    body = response.json()
    (line,) = body["lines"]
    assert line["type_name"] == "Tritanium"
    assert line["unit_buy_price"] == 5.0
    assert line["profit"] > 0
    assert body["total_investment"] <= 20_000_000
    assert body["cargo_m3_used"] <= 5000
    with DuckDBStore(settings.storage.duckdb_path) as store:
        assert store.plan_run(body["run_id"]) is not None


def test_haul_cargo_value_cap_is_a_per_request_override(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_haul_orders(store)

    body = (
        _client(settings)
        .post("/api/haul", json=_haul_body(exit_mode="instant", max_cargo_value_isk=1000))
        .json()
    )

    assert body["total_investment"] <= 1000 + 1e-6


def test_haul_same_station_is_400(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).post("/api/haul", json=_haul_body(to_station_id=STATION_A))

    assert response.status_code == 400


def test_haul_rejects_invalid_body(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    client = _client(settings)

    assert client.post("/api/haul", json=_haul_body(budget_isk=0)).status_code == 422
    assert client.post("/api/haul", json=_haul_body(exit_mode="sideways")).status_code == 422


def test_market_making_rows_join_competition_and_spread(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_mm_candidate(store)

    rows = _client(settings).get(f"/api/market-making?station={STATION_A}").json()

    (row,) = rows
    assert row["type_name"] == "Tritanium"
    assert row["station_name"] == "Jita"
    assert row["relists_per_hour"] == 0.02
    assert row["net_spread_pct"] > 0
    assert row["profit_per_day_per_isk"] > 0


def test_trades_expected_vs_actual(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_plan_run("run-1", "haul", datetime.now(UTC), STATION_A, STATION_B, 1.0, "x")
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
        store.write_trade("t1", "run-1", 34, datetime.now(UTC), 100, 5.1, 7.9, "slow")
        store.write_trade("t2", None, 34, datetime.now(UTC) - timedelta(days=1), 10, 5, 6, None)

    rows = _client(settings).get("/api/trades").json()

    first, second = rows  # newest first
    assert first["expected_profit"] == 300.0
    assert abs(first["actual_profit"] - 280.0) < 1e-9
    assert first["notes"] == "slow"
    assert first["logged_at"].endswith("Z")
    assert second["expected_profit"] is None  # no plan line -> no expectation
    assert second["type_name"] == "34"  # no plan name and no types row: falls back to the id


def test_settings_exposes_dashboard_subset(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    body = _client(settings).get("/api/settings").json()

    assert set(body) == {
        "skills",
        "standings",
        "station_pairs",
        "filters",
        "hauling",
        "market_making",
        "budget",
        "cargo",
        "health",
        "ui",
    }
    assert body["ui"]["haul_actionable_roi_pct"] == 8.0
    assert "contact" not in str(body)  # esi/storage config is not exposed
