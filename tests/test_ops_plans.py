"""Tests for save_hauling_plan/save_market_making_plan: round-trip through duckdb."""

from __future__ import annotations

from pathlib import Path

import pytest

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Filters,
    MarketMaking,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.hauling.optimizer import HaulingPlan, ShoppingListLine
from eve.market_making.optimizer import MarketMakingLine, MarketMakingPlan
from eve.ops.plans import save_hauling_plan, save_market_making_plan
from eve.store.duckdb_store import DuckDBStore

STATION_A, STATION_B = 60003760, 60008494


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
        market_making=MarketMaking(),
    )


def test_save_hauling_plan_round_trips(tmp_path: Path) -> None:
    plan = HaulingPlan(
        from_station_id=STATION_A,
        to_station_id=STATION_B,
        lines=[
            ShoppingListLine(
                type_id=34,
                type_name="Tritanium",
                quantity=1000.0,
                max_buy_price=5.0,
                exit_mode="instant",
                target_sell_price=6.5,
            )
        ],
        total_investment=5000.0,
        cargo_m3_used=10.0,
        net_profit=1500.0,
        jumps=3,
        profit_per_hour=30000.0,
    )

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        run_id = save_hauling_plan(store, plan)
        saved_run = store.plan_run(run_id)
        saved_lines = store.plan_lines_for_run(run_id)

    assert saved_run == (run_id, "haul", saved_run[2], STATION_A, STATION_B, 1500.0, "per_run")
    assert saved_lines == [(34, "Tritanium", 1000.0, 5.0, 6.5)]


def test_save_market_making_plan_round_trips_and_infers_quantity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plan = MarketMakingPlan(
        station_id=STATION_A,
        lines=[
            MarketMakingLine(
                type_id=34,
                type_name="Tritanium",
                buy_price=5.0,
                sell_price=8.0,
                capital_allocated=78_000.0,
                net_profit_per_day=10_540.5,
            )
        ],
        total_capital_used=78_000.0,
        slots_used=2,
        total_net_profit_per_day=10_540.5,
    )

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        run_id = save_market_making_plan(store, plan, settings)
        saved_run = store.plan_run(run_id)
        saved_lines = store.plan_lines_for_run(run_id)

    assert saved_run == (run_id, "mm", saved_run[2], STATION_A, None, 10_540.5, "per_day")
    assert len(saved_lines) == 1
    type_id, type_name, quantity, buy_price, sell_price = saved_lines[0]
    assert (type_id, type_name, buy_price, sell_price) == (34, "Tritanium", 5.0, 8.0)
    # no margin trading (escrow 100%) -- capital_required/unit = buy + sell = 13.0
    assert quantity == pytest.approx(78_000.0 / 13.0)
