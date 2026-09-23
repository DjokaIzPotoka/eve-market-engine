"""Dashboard/CLI tests for the hauling exit mode and hold days options."""

from __future__ import annotations

from pathlib import Path

from test_web_plans import _client, _seed_haul_orders, _seed_topology, _settings
from typer.testing import CliRunner

from eve.cli import app
from eve.store.duckdb_store import DuckDBStore

STATION_A, STATION_B = 60003760, 60008494


def _post(tmp_path: Path, **extra: object):
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_haul_orders(store)
        return _client(settings).post(
            "/haul",
            data={
                "from_station_id": STATION_A,
                "to_station_id": STATION_B,
                "budget": 20_000_000,
                "cargo_m3": 5000,
                **extra,
            },
        )


def test_haul_form_offers_exit_modes_and_hold_days(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
    text = _client(settings).get("/haul").text

    for value in ("instant", "listing", "both"):
        assert f'name="exit_mode" value="{value}"' in text
    for days in (1, 3, 7):
        assert f'<option value="{days}"' in text


def test_instant_mode_result_shows_mode_without_holding(tmp_path: Path) -> None:
    response = _post(tmp_path, exit_mode="instant", hold_days=3)

    assert response.status_code == 200
    assert "Mode: instant" in response.text
    assert "holding 3" not in response.text
    assert "Tritanium" in response.text


def test_listing_mode_result_shows_hold_days(tmp_path: Path) -> None:
    response = _post(tmp_path, exit_mode="listing", hold_days=3)

    assert response.status_code == 200
    assert "Mode: listing, holding 3 days" in response.text


def test_unknown_exit_mode_returns_400(tmp_path: Path) -> None:
    response = _post(tmp_path, exit_mode="sideways")

    assert response.status_code == 400


def test_cli_rejects_unknown_exit_mode() -> None:
    result = CliRunner().invoke(
        app,
        ["haul", "--from", "1", "--to", "2", "--budget", "1", "--cargo", "1", "--exit-mode", "x"],
    )

    assert result.exit_code != 0


def test_result_table_shows_daily_volume_columns_with_na_without_history(tmp_path: Path) -> None:
    response = _post(tmp_path, exit_mode="instant")  # seeded orders, no history rows

    assert "Daily vol (units)" in response.text
    assert "Daily vol (ISK)" in response.text
    assert "Qty / daily vol" in response.text
    assert "n/a" in response.text
