"""Tests for the trade-log form and the expected-vs-actual report page."""

from __future__ import annotations

from datetime import UTC, datetime
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

STATION_A, STATION_B = 60003760, 60008494
RUN_ID = "run-1"
TYPE_ID = 34


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_B)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path=str(tmp_path / "eve.duckdb")),
        health=Health(),
    )


def _client(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_trade_form_renders(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    response = _client(settings).get("/trades")

    assert response.status_code == 200


def test_trade_submit_redirects_to_report_and_report_shows_expected_vs_actual(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_plan_run(
            RUN_ID, "haul", datetime.now(UTC), STATION_A, STATION_B, 999.0, "per_run"
        )
        store.write_plan_lines(
            RUN_ID,
            [
                {
                    "type_id": TYPE_ID,
                    "type_name": "Tritanium",
                    "suggested_quantity": 100.0,
                    "suggested_buy_price": 5.0,
                    "suggested_sell_price": 8.0,
                }
            ],
        )

    client = _client(settings)
    response = client.post(
        "/trades",
        data={
            "type_id": TYPE_ID,
            "quantity": 100,
            "buy_price": 5.1,
            "sell_price": 7.9,
            "run_id": RUN_ID,
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert response.url.path == "/report"
    # expected = (8.0 - 5.0) * 100 = 300.0; actual = (7.9 - 5.1) * 100 = 280.0
    assert "300.00" in response.text
    assert "280.00" in response.text


def test_trade_without_run_id_still_shows_actual_profit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    client = _client(settings)
    response = client.post(
        "/trades",
        data={"type_id": 99, "quantity": 50, "buy_price": 2.0, "sell_price": 3.0},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "50.00" in response.text  # actual = (3.0 - 2.0) * 50


def test_report_days_filter_excludes_older_trades(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path):
        pass

    client = _client(settings)
    client.post(
        "/trades",
        data={"type_id": TYPE_ID, "quantity": 10, "buy_price": 1.0, "sell_price": 2.0},
        follow_redirects=True,
    )

    response = client.get("/report?days=0")

    assert response.status_code == 200
    assert "No trades logged yet" in response.text
