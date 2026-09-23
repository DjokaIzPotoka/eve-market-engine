"""Tests for the hauling optimizer: brute-force match, MILP-vs-greedy, budget/cargo safety,
empty-liquidity edge cases, the listing exit mode, and multi-pair ranking.

Same synthetic-seed-via-`store._con.execute` + `Settings.model_construct` convention as
test_book_features.py / test_fees.py -- no fixture files.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Features,
    Fees,
    Fill,
    Filters,
    Hauling,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.fees.fees import broker_fee_pct, sales_tax_pct
from eve.hauling.optimizer import rank_station_pairs, solve_hauling
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 10000090
SYS_A, SYS_B, SYS_C = 30000001, 30000002, 30000003
SYS_X1, SYS_X2, SYS_X3, SYS_X4 = 30000011, 30000012, 30000013, 30000014
STATION_A, STATION_B, STATION_C = 60000201, 60000202, 60000203

FETCHED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _settings(**hauling_overrides: object) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_B)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path="unused"),
        features=Features(),
        fill=Fill(),
        fees=Fees(),
        hauling=Hauling(**hauling_overrides),
    )


def _seed_system_station(store: DuckDBStore, system_id: int, station_id: int | None = None) -> None:
    store._con.execute(
        "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)",
        [system_id, REGION_ID, 1, 0.5, f"sys-{system_id}"],
    )
    if station_id is not None:
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)",
            [station_id, f"station-{station_id}", system_id],
        )


def _link(store: DuckDBStore, a: int, b: int) -> None:
    store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [a, b])
    store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [b, a])


def _order(
    store: DuckDBStore,
    order_id: int,
    *,
    location_id: int,
    system_id: int,
    is_buy: bool,
    price: float,
    volume: float,
    type_id: int,
    range_: str = "station",
    min_volume: int = 1,
) -> None:
    store._con.execute(
        """
        INSERT INTO orders_latest
        (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
         volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 90, ?, ?, 'snap-1', ?)
        """,
        [
            order_id,
            type_id,
            location_id,
            system_id,
            is_buy,
            price,
            volume,
            volume,
            min_volume,
            range_,
            REGION_ID,
            FETCHED_AT.replace(tzinfo=None),
        ],
    )


def _seed_type(store: DuckDBStore, type_id: int, packaged_volume: float, name: str = "") -> None:
    store._con.execute(
        "INSERT INTO types VALUES (?, ?, ?, ?, ?)",
        [type_id, name or f"type-{type_id}", packaged_volume, 0, True],
    )


def _seed_snapshot(store: DuckDBStore, region_id: int, fetched_at: datetime) -> None:
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        [
            f"snap-{region_id}",
            region_id,
            fetched_at.replace(tzinfo=None),
            (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
            1,
        ],
    )


def _fill_revenue(levels_desc: list[tuple[float, float]], qty: float, sales_tax: float) -> float:
    """Fill `qty` units against exit levels (already best-price-first), sales tax only."""
    revenue = 0.0
    remaining = qty
    for price, cap in levels_desc:
        if remaining <= 0:
            break
        take = min(cap, remaining)
        revenue += take * price * (1 - sales_tax / 100)
        remaining -= take
    return revenue


def test_matches_brute_force_optimum(tmp_path: Path) -> None:
    """Two expensive (integer) items with generous budget/cargo -- exhaustively enumerate
    every purchase combination and confirm the MILP finds the true maximum."""
    settings = _settings(integer_price_threshold_isk=1_000_000.0)
    tax = sales_tax_pct(settings.skills, settings.fees)

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)

        TYPE_1, TYPE_2 = 41, 42
        _seed_type(store, TYPE_1, packaged_volume=1.0)
        _seed_type(store, TYPE_2, packaged_volume=2.0)

        # TYPE_1: two source levels, two destination instant levels
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=2_000_000,
            volume=3,
            type_id=TYPE_1,
        )
        _order(
            store,
            2,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=2_100_000,
            volume=2,
            type_id=TYPE_1,
        )
        _order(
            store,
            101,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=2_500_000,
            volume=2,
            type_id=TYPE_1,
        )
        _order(
            store,
            102,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=2_400_000,
            volume=4,
            type_id=TYPE_1,
        )

        # TYPE_2: one source level, one destination instant level
        _order(
            store,
            3,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=1_500_000,
            volume=4,
            type_id=TYPE_2,
        )
        _order(
            store,
            103,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=1_900_000,
            volume=3,
            type_id=TYPE_2,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=50_000_000, cargo_m3=100.0, settings=settings
        )

    exit1 = [(2_500_000.0, 2.0), (2_400_000.0, 4.0)]
    exit2 = [(1_900_000.0, 3.0)]
    best = -1.0
    for x1 in range(4):
        for x2 in range(3):
            for x3 in range(5):
                cost = x1 * 2_000_000 + x2 * 2_100_000 + x3 * 1_500_000
                revenue = _fill_revenue(exit1, x1 + x2, tax) + _fill_revenue(exit2, x3, tax)
                best = max(best, revenue - cost)

    assert plan.net_profit == pytest.approx(best, rel=1e-6)
    assert plan.total_investment <= 50_000_000
    assert plan.cargo_m3_used <= 100.0


def test_milp_beats_greedy_when_budget_and_cargo_both_bind(tmp_path: Path) -> None:
    """Classic two-constraint knapsack mismatch: a naive profit-per-ISK greedy fills cargo
    with the bulky item and leaves budget unused, while the true optimum picks the dense
    item exclusively."""
    settings = _settings()
    tax = sales_tax_pct(settings.skills, settings.fees)

    TYPE_BULK, TYPE_DENSE = 43, 44
    # profit/unit = 5 (bulk) and 3 (dense) at cost=1 ISK/unit after 7.5% sales tax
    price_bulk = 6.0 / (1 - tax / 100)
    price_dense = 4.0 / (1 - tax / 100)

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)

        _seed_type(store, TYPE_BULK, packaged_volume=10.0)
        _seed_type(store, TYPE_DENSE, packaged_volume=1.0)

        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=1.0,
            volume=200,
            type_id=TYPE_BULK,
        )
        _order(
            store,
            101,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=price_bulk,
            volume=200,
            type_id=TYPE_BULK,
        )
        _order(
            store,
            2,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=1.0,
            volume=200,
            type_id=TYPE_DENSE,
        )
        _order(
            store,
            102,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=price_dense,
            volume=200,
            type_id=TYPE_DENSE,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=100.0, cargo_m3=50.0, settings=settings
        )

    # greedy: sort by profit/ISK (bulk=5 > dense=3), buy bulk until a constraint binds
    items = [
        {"cost": 1.0, "cargo": 10.0, "profit": 5.0, "avail": 200.0},
        {"cost": 1.0, "cargo": 1.0, "profit": 3.0, "avail": 200.0},
    ]
    items.sort(key=lambda it: it["profit"] / it["cost"], reverse=True)
    budget_left, cargo_left, greedy_profit = 100.0, 50.0, 0.0
    for it in items:
        qty = min(it["avail"], budget_left / it["cost"], cargo_left / it["cargo"])
        qty = max(qty, 0.0)
        greedy_profit += qty * it["profit"]
        budget_left -= qty * it["cost"]
        cargo_left -= qty * it["cargo"]

    assert greedy_profit == pytest.approx(25.0)
    assert plan.net_profit >= greedy_profit
    assert plan.net_profit > greedy_profit + 50.0  # strictly, substantially better
    assert plan.total_investment <= 100.0 + 1e-6
    assert plan.cargo_m3_used <= 50.0 + 1e-6


def test_zero_budget_yields_empty_plan(tmp_path: Path) -> None:
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 45, packaged_volume=1.0)
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=10.0,
            volume=50,
            type_id=45,
        )
        _order(
            store,
            101,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=20.0,
            volume=50,
            type_id=45,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=0.0, cargo_m3=1000.0, settings=settings
        )

    assert plan.lines == []
    assert plan.total_investment == 0.0
    assert plan.net_profit == 0.0


def test_cargo_smaller_than_any_unit_yields_zero_purchases(tmp_path: Path) -> None:
    """An expensive (integer) item whose single-unit volume exceeds the cargo cap must
    round down to exactly zero, not a fractional purchase."""
    settings = _settings(integer_price_threshold_isk=1_000_000.0)
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 46, packaged_volume=5.0)
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=2_000_000,
            volume=10,
            type_id=46,
        )
        _order(
            store,
            101,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=3_000_000,
            volume=10,
            type_id=46,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=1_000_000_000, cargo_m3=1.0, settings=settings
        )

    assert plan.lines == []
    assert plan.cargo_m3_used == 0.0


def test_no_source_liquidity_yields_empty_plan(tmp_path: Path) -> None:
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 47, packaged_volume=1.0)
        _order(
            store,
            101,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=20.0,
            volume=50,
            type_id=47,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=1000.0, cargo_m3=1000.0, settings=settings
        )

    assert plan.lines == []


def test_type_with_null_packaged_volume_is_dropped_not_a_crash(tmp_path: Path) -> None:
    """SDE types can have a NULL packaged_volume; cargo can't be enforced, so drop them."""
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 60, packaged_volume=1.0)
        store._con.execute("INSERT INTO types VALUES (61, 'no-volume', NULL, 0, TRUE)")
        for type_id, sell_id, buy_id in ((60, 1, 2), (61, 3, 4)):
            _order(
                store,
                sell_id,
                location_id=STATION_A,
                system_id=SYS_A,
                is_buy=False,
                price=10.0,
                volume=50,
                type_id=type_id,
            )
            _order(
                store,
                buy_id,
                location_id=STATION_B,
                system_id=SYS_B,
                is_buy=True,
                price=20.0,
                volume=50,
                type_id=type_id,
            )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=10_000.0, cargo_m3=1000.0, settings=settings
        )

    assert [line.type_id for line in plan.lines] == [60]


def test_no_destination_liquidity_yields_empty_plan(tmp_path: Path) -> None:
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 48, packaged_volume=1.0)
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=10.0,
            volume=50,
            type_id=48,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=1000.0, cargo_m3=1000.0, settings=settings
        )

    assert plan.lines == []


def test_listing_exit_mode_used_when_no_instant_buyer_exists(tmp_path: Path) -> None:
    settings = _settings()
    tax = sales_tax_pct(settings.skills, settings.fees)
    broker_pct = broker_fee_pct(
        settings.skills, settings.standings, settings.fees, is_structure=False
    )

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 49, packaged_volume=0.1)

        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=1.0,
            volume=50,
            type_id=49,
        )
        # listing reference price only -- no buy orders anywhere covering STATION_B
        _order(
            store,
            201,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=False,
            price=10.0,
            volume=5,
            type_id=49,
        )

        _seed_snapshot(store, REGION_ID, FETCHED_AT)
        target_date = FETCHED_AT.date() - timedelta(days=1)
        store.write_history(
            REGION_ID,
            49,
            target_date,
            [
                {
                    "date": target_date.isoformat(),
                    "average": 10.0,
                    "highest": 10.0,
                    "lowest": 10.0,
                    "order_count": 1,
                    "volume": 1000,
                }
            ],
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=1000.0, cargo_m3=1000.0, settings=settings
        )

    assert len(plan.lines) == 1
    line = plan.lines[0]
    assert line.exit_mode == "listing"
    assert line.quantity == pytest.approx(50.0)

    effective_price = 10.0 * (1 - settings.hauling.listing_discount_pct_per_day / 100)
    revenue_per_unit = effective_price * (1 - tax / 100 - broker_pct / 100)
    expected_profit = 50 * (revenue_per_unit - 1.0)
    assert plan.net_profit == pytest.approx(expected_profit, rel=1e-6)


def test_ranking_prefers_higher_profit_per_hour_over_higher_raw_profit(tmp_path: Path) -> None:
    settings = _settings()
    tax = sales_tax_pct(settings.skills, settings.fees)

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _seed_system_station(store, SYS_C, STATION_C)
        for sid in (SYS_X1, SYS_X2, SYS_X3, SYS_X4):
            _seed_system_station(store, sid)
        # A -- C is a direct 1-jump link; A -- X1 -- X2 -- X3 -- X4 -- B is 5 jumps
        _link(store, SYS_A, SYS_C)
        _link(store, SYS_A, SYS_X1)
        _link(store, SYS_X1, SYS_X2)
        _link(store, SYS_X2, SYS_X3)
        _link(store, SYS_X3, SYS_X4)
        _link(store, SYS_X4, SYS_B)

        TYPE_FAR, TYPE_NEAR = 51, 52
        _seed_type(store, TYPE_FAR, packaged_volume=0.01)
        _seed_type(store, TYPE_NEAR, packaged_volume=0.01)

        # 1 unit bought & sold each -- profit exactly 50 (far, low profit/hour)
        # and 20 (near, high profit/hour)
        price_far = 51.0 / (1 - tax / 100)
        price_near = 21.0 / (1 - tax / 100)
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=1.0,
            volume=1,
            type_id=TYPE_FAR,
        )
        _order(
            store,
            101,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=price_far,
            volume=1,
            type_id=TYPE_FAR,
        )
        _order(
            store,
            2,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=1.0,
            volume=1,
            type_id=TYPE_NEAR,
        )
        _order(
            store,
            102,
            location_id=STATION_C,
            system_id=SYS_C,
            is_buy=True,
            price=price_near,
            volume=1,
            type_id=TYPE_NEAR,
        )

        plans = rank_station_pairs(
            store,
            [
                StationPair(from_station_id=STATION_A, to_station_id=STATION_B),
                StationPair(from_station_id=STATION_A, to_station_id=STATION_C),
            ],
            budget=1000.0,
            cargo_m3=1000.0,
            settings=settings,
        )

    assert plans[0].to_station_id == STATION_C
    assert plans[1].to_station_id == STATION_B
    assert plans[0].net_profit < plans[1].net_profit
    assert plans[0].profit_per_hour > plans[1].profit_per_hour


def test_lines_report_roi_and_margin_and_never_show_sub_unit_quantities(tmp_path: Path) -> None:
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 70, packaged_volume=1.0)
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=10.0,
            volume=100,
            type_id=70,
        )
        _order(
            store,
            2,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=20.0,
            volume=100,
            type_id=70,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=100_000.0, cargo_m3=1000.0, settings=settings
        )

    assert plan.lines
    for line in plan.lines:
        assert line.quantity >= 1.0
        assert line.roi_pct == pytest.approx(line.net_profit / (line.quantity * 10.0) * 100)
    assert plan.roi_pct == pytest.approx(plan.net_profit / plan.total_investment * 100)
    assert 0 < plan.margin_pct < 100


def test_dust_quantities_are_dropped_from_lines_and_totals(tmp_path: Path) -> None:
    """With a budget worth ~0.4 of a unit, the LP would buy a fraction -- nothing is shown."""
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_system_station(store, SYS_A, STATION_A)
        _seed_system_station(store, SYS_B, STATION_B)
        _link(store, SYS_A, SYS_B)
        _seed_type(store, 71, packaged_volume=1.0)
        _order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=False,
            price=10.0,
            volume=100,
            type_id=71,
        )
        _order(
            store,
            2,
            location_id=STATION_B,
            system_id=SYS_B,
            is_buy=True,
            price=20.0,
            volume=100,
            type_id=71,
        )

        plan = solve_hauling(
            store, STATION_A, STATION_B, budget=4.0, cargo_m3=1000.0, settings=settings
        )

    assert plan.lines == []
    assert plan.total_investment == 0.0
    assert plan.net_profit == 0.0
