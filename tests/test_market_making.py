"""Tests for the market making advisor: order-slot/escrow formulas, candidate filters,
competition-sensitive ranking, MILP-vs-brute-force, and budget/slot safety.

Same synthetic-seed-via-`store._con.execute` + `Settings.model_construct` convention as
test_hauling.py / test_fees.py -- no fixture files.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import combinations
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
    MarketMaking,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.market_making.candidates import Candidate, load_candidates
from eve.market_making.capital import buy_order_escrow_pct, max_order_slots
from eve.market_making.optimizer import build_problem, solve_market_making
from eve.optimize.solver import solve
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 10000091
SYS_A = 30000021
STATION_A = 60000301
FETCHED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _settings(skills: Skills | None = None, **mm_overrides: object) -> Settings:
    return Settings.model_construct(
        skills=skills or Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_A)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path="unused"),
        features=Features(),
        fill=Fill(),
        fees=Fees(),
        hauling=Hauling(),
        market_making=MarketMaking(**mm_overrides),
    )


def _seed_station(store: DuckDBStore) -> None:
    store._con.execute(
        "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_A, REGION_ID, 1, 0.5, "sys-A"]
    )
    store._con.execute(
        "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "station-A", SYS_A]
    )


def _seed_snapshot(store: DuckDBStore, snapshot_id: str, fetched_at: datetime) -> None:
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        [
            snapshot_id,
            REGION_ID,
            fetched_at.replace(tzinfo=None),
            (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
            1,
        ],
    )


def _seed_book_features(
    store: DuckDBStore,
    snapshot_id: str,
    type_id: int,
    *,
    best_bid: float | None,
    best_ask: float | None,
    bid_near_top: int,
    ask_near_top: int,
    history_avg_30d: float | None,
) -> None:
    store._con.execute(
        """
        INSERT INTO book_features
        (snapshot_id, location_id, type_id, best_bid, best_ask, bid_depth_volumes,
         ask_depth_volumes, bid_orders_near_top, ask_orders_near_top, history_avg_7d,
         history_avg_30d, price_deviation_30d_pct, computed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            snapshot_id,
            STATION_A,
            type_id,
            best_bid,
            best_ask,
            [],
            [],
            bid_near_top,
            ask_near_top,
            history_avg_30d,
            history_avg_30d,
            0.0,
            FETCHED_AT.replace(tzinfo=None),
        ],
    )


def _seed_book_diff_metrics(
    store: DuckDBStore,
    type_id: int,
    *,
    bid_volume_per_hour: float,
    ask_volume_per_hour: float,
    relists_per_hour: float,
    bid_capture_rate: float,
    ask_capture_rate: float,
) -> None:
    store._con.execute(
        """
        INSERT INTO book_diff_metrics
        (region_id, location_id, type_id, from_snapshot_id, to_snapshot_id, from_fetched_at,
         to_fetched_at, elapsed_hours, bid_volume_per_hour, ask_volume_per_hour,
         relists_per_hour, bid_competitor_count, ask_competitor_count,
         estimated_bid_capture_rate, estimated_ask_capture_rate)
        VALUES (?, ?, ?, 'from-1', 'to-1', ?, ?, 1.0, ?, ?, ?, 1, 1, ?, ?)
        """,
        [
            REGION_ID,
            STATION_A,
            type_id,
            FETCHED_AT.replace(tzinfo=None),
            FETCHED_AT.replace(tzinfo=None),
            bid_volume_per_hour,
            ask_volume_per_hour,
            relists_per_hour,
            bid_capture_rate,
            ask_capture_rate,
        ],
    )


def _seed_profitable_type(store: DuckDBStore, type_id: int, *, relists_per_hour: float) -> None:
    """A wide-spread (60%), liquid item -- comfortably profitable at low relist rates."""
    _seed_book_features(
        store,
        "snap-1",
        type_id,
        best_bid=1000.0,
        best_ask=1600.0,
        bid_near_top=3,
        ask_near_top=3,
        history_avg_30d=1300.0,
    )
    _seed_book_diff_metrics(
        store,
        type_id,
        bid_volume_per_hour=2.0,
        ask_volume_per_hour=2.0,
        relists_per_hour=relists_per_hour,
        bid_capture_rate=0.5,
        ask_capture_rate=0.5,
    )


# -- max_order_slots / buy_order_escrow_pct -----------------------------------------


def test_max_order_slots_base_only() -> None:
    assert max_order_slots(Skills(), MarketMaking()) == 5


def test_max_order_slots_trade_and_retail_v() -> None:
    # 5 base + 4*5 (Trade V) + 8*5 (Retail V) = 5 + 20 + 40 = 65
    slots = max_order_slots(Skills(trade=5, retail=5), MarketMaking())
    assert slots == 65


def test_max_order_slots_all_skills_v_reaches_305() -> None:
    skills = Skills(trade=5, retail=5, wholesale=5, tycoon=5)
    assert max_order_slots(skills, MarketMaking()) == 305


def test_buy_order_escrow_pct_no_skill_is_full_value() -> None:
    assert buy_order_escrow_pct(Skills(), MarketMaking()) == 100.0


def test_buy_order_escrow_pct_at_margin_trading_v() -> None:
    # 100 * 0.75^5 ~= 23.73%, matching the "10M ISK order -> 2.4M escrow" wiki example
    pct = buy_order_escrow_pct(Skills(margin_trading=5), MarketMaking())
    assert round(pct, 2) == round(100 * 0.75**5, 2)
    assert 23.0 < pct < 24.0


# -- candidate filters ----------------------------------------------------------------


def test_load_candidates_rejects_missing_history_and_thin_book(tmp_path: Path) -> None:
    settings = _settings()
    TYPE_OK, TYPE_NO_HISTORY, TYPE_THIN_BOOK = 81, 82, 83

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)

        _seed_profitable_type(store, TYPE_OK, relists_per_hour=0.02)

        _seed_book_features(
            store,
            "snap-1",
            TYPE_NO_HISTORY,
            best_bid=1000.0,
            best_ask=1600.0,
            bid_near_top=3,
            ask_near_top=3,
            history_avg_30d=None,
        )
        _seed_book_diff_metrics(
            store,
            TYPE_NO_HISTORY,
            bid_volume_per_hour=2.0,
            ask_volume_per_hour=2.0,
            relists_per_hour=0.02,
            bid_capture_rate=0.5,
            ask_capture_rate=0.5,
        )

        _seed_book_features(
            store,
            "snap-1",
            TYPE_THIN_BOOK,
            best_bid=1000.0,
            best_ask=1600.0,
            bid_near_top=1,  # only one order at top -- must be rejected
            ask_near_top=3,
            history_avg_30d=1300.0,
        )
        _seed_book_diff_metrics(
            store,
            TYPE_THIN_BOOK,
            bid_volume_per_hour=2.0,
            ask_volume_per_hour=2.0,
            relists_per_hour=0.02,
            bid_capture_rate=0.5,
            ask_capture_rate=0.5,
        )

        candidates = load_candidates(store, STATION_A, capital=1_000_000.0, settings=settings)

    type_ids = {c.type_id for c in candidates}
    assert type_ids == {TYPE_OK}


# -- competition-sensitive ranking -----------------------------------------------------


def test_more_relists_lowers_profit_and_ranking(tmp_path: Path) -> None:
    settings = _settings()
    TYPE_LOW_RELISTS, TYPE_HIGH_RELISTS = 84, 85

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)
        _seed_profitable_type(store, TYPE_LOW_RELISTS, relists_per_hour=0.02)
        _seed_profitable_type(store, TYPE_HIGH_RELISTS, relists_per_hour=0.3)

        candidates = load_candidates(store, STATION_A, capital=1_000_000.0, settings=settings)

    by_type = {c.type_id: c for c in candidates}
    assert set(by_type) == {TYPE_LOW_RELISTS, TYPE_HIGH_RELISTS}

    low, high = by_type[TYPE_LOW_RELISTS], by_type[TYPE_HIGH_RELISTS]
    # identical spread/fill assumptions -- only relists_per_hour differs
    assert low.buy_price == high.buy_price
    assert low.sell_price == high.sell_price
    assert low.expected_daily_fill == high.expected_daily_fill
    assert low.net_profit_per_day > high.net_profit_per_day
    assert low.profit_per_day_per_isk > high.profit_per_day_per_isk


# -- MILP vs brute force ---------------------------------------------------------------


def _candidate(type_id: int, *, capital_required: float, net_profit_per_day: float) -> Candidate:
    return Candidate(
        type_id=type_id,
        type_name=f"type-{type_id}",
        buy_price=10.0,
        sell_price=12.0,
        expected_daily_fill=1.0,
        turnover_days=1.0,
        net_profit_per_day=net_profit_per_day,
        capital_required=capital_required,
        profit_per_day_per_isk=net_profit_per_day / capital_required,
    )


def test_milp_matches_brute_force_over_subsets() -> None:
    # 4 candidates, capital and slots (2 items max) both bind -- enumerate every subset
    # obeying the slot cap and greedily fund it by profit/ISK ratio, compare to the MILP.
    settings = _settings(base_order_slots=4)  # 4 slots / 2 per item = 2 items max
    candidates = [
        _candidate(1, capital_required=50.0, net_profit_per_day=5.0),  # ratio 0.10
        _candidate(2, capital_required=30.0, net_profit_per_day=6.0),  # ratio 0.20
        _candidate(3, capital_required=40.0, net_profit_per_day=6.0),  # ratio 0.15
        _candidate(4, capital_required=20.0, net_profit_per_day=1.0),  # ratio 0.05
    ]
    capital = 70.0

    problem, _ = build_problem(candidates, capital, settings)
    solution = solve(problem)

    best = 0.0
    for r in range(len(candidates) + 1):
        for subset in combinations(candidates, r):
            if 2 * len(subset) > 4:
                continue
            ordered = sorted(subset, key=lambda c: c.profit_per_day_per_isk, reverse=True)
            remaining, profit = capital, 0.0
            for c in ordered:
                take = min(c.capital_required, remaining)
                profit += take * c.profit_per_day_per_isk
                remaining -= take
            best = max(best, profit)

    assert best == pytest.approx(12.0)  # items 2+3 fully funded: 6 + 6
    assert solution.objective_value == pytest.approx(best, rel=1e-6)


# -- end-to-end safety -------------------------------------------------------------------


def test_solve_market_making_never_exceeds_capital_or_slots(tmp_path: Path) -> None:
    settings = _settings(base_order_slots=5)
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)
        for offset, type_id in enumerate((91, 92, 93, 94, 95)):
            _seed_profitable_type(store, type_id, relists_per_hour=0.02 + offset * 0.01)

        plan = solve_market_making(store, STATION_A, capital=100_000.0, settings=settings)

    max_slots = max_order_slots(settings.skills, settings.market_making)
    assert plan.slots_used <= max_slots
    assert plan.total_capital_used <= 100_000.0
    for line in plan.lines:
        assert (
            line.capital_allocated
            <= settings.market_making.max_capital_share_per_item * 100_000.0 + 1e-6
        )


def test_solve_market_making_with_no_candidates_is_empty(tmp_path: Path) -> None:
    settings = _settings()
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)
        # no book_features / book_diff_metrics rows at all

        plan = solve_market_making(store, STATION_A, capital=100_000.0, settings=settings)

    assert plan.lines == []
    assert plan.total_capital_used == 0.0
    assert plan.total_net_profit_per_day == 0.0
