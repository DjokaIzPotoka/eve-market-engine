"""Hauling optimizer (Module B, Faza 6): MILP shopping list for one station pair.

Variables are order-level, not aggregated price levels: `x_{order_id}` buys into a source
sell order, `y_{order_id}` sells instant into a destination-covering buy order, `z_{type_id}`
sells via a self-placed listing at the destination (one variable per item -- a listing is a
single order, not a ladder of levels). A per-item balance constraint (purchased >= sold)
ties them together; since buying always costs money, the objective never rewards purchasing
without an exit.

Fee note: `broker_fee_isk`'s ISK floor is piecewise/non-linear in quantity, which would
require binary + big-M terms to model exactly. The objective instead uses the pure
percentage forms (`broker_fee_pct`, `sales_tax_pct`) with no floor -- a deliberate, documented
approximation that's immaterial for anything actually worth hauling.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from eve.config import Settings, StationPair
from eve.features.jump_graph import bfs_within
from eve.fees.fees import broker_fee_pct, is_structure_location, sales_tax_pct
from eve.hauling.data import HaulingMarketData, load_hauling_market_data
from eve.optimize.problem import Constraint, Objective, Problem, Variable
from eve.optimize.solver import solve
from eve.store.duckdb_store import DuckDBStore

_EPS = 1e-6
# CBC reports values with ~8 significant digits, so on million-unit quantities the solution
# can sit ~1e-2 past a bound it actually respects. Post-solve checks therefore allow a tiny
# *relative* slack (1e-7) on top of the absolute _EPS -- still far tighter than any real
# violation, which is why the assertions stay hard errors.
_REL_TOL = 1e-7


def _tol(bound: float) -> float:
    return max(_EPS, _REL_TOL * abs(bound))


ExitMode = Literal["instant", "listing", "both"]
EXIT_MODES: tuple[ExitMode, ...] = ("instant", "listing", "both")


@dataclass(frozen=True)
class ShoppingListLine:
    type_id: int
    type_name: str
    quantity: float
    max_buy_price: float
    exit_mode: str  # "instant" | "listing" | "mixed"
    target_sell_price: float
    net_profit: float = 0.0
    roi_pct: float = 0.0  # net profit / investment
    margin_pct: float = 0.0  # net profit / gross sale revenue
    # Destination market activity (ESI history, averaged over `history_window_days`):
    # units traded per day, the same in ISK, and how much of it this line would be.
    daily_volume: float | None = None
    daily_volume_isk: float | None = None
    share_of_daily_volume_pct: float | None = None


@dataclass(frozen=True)
class HaulingPlan:
    from_station_id: int
    to_station_id: int
    lines: list[ShoppingListLine]
    total_investment: float
    cargo_m3_used: float
    net_profit: float
    jumps: int
    profit_per_hour: float
    roi_pct: float = 0.0
    margin_pct: float = 0.0
    exit_mode: str = "both"
    hold_days: int = 1
    # Informational only -- lanes are still ranked by profit per hour of travel (CLAUDE.md).
    profit_per_day_incl_hold: float = 0.0


def listing_capacity(daily_volume: float, settings: Settings, hold_days: int) -> float:
    """Units a self-listed order can plausibly sell within `hold_days` (constant, pre-solve)."""
    return settings.hauling.listing_daily_volume_share * daily_volume * hold_days


def listing_effective_price(
    ref_price: float, volatility_pct: float, settings: Settings, hold_days: int
) -> float:
    """Reference ask minus a time-value haircut (per day held) and a price-risk haircut
    (volatility x sqrt(days)). Both are constants per item, so the MILP stays linear."""
    h = settings.hauling
    haircut_pct = h.listing_discount_pct_per_day * hold_days + (
        h.listing_volatility_risk_factor * volatility_pct * math.sqrt(hold_days)
    )
    return ref_price * (1 - min(haircut_pct, 99.0) / 100)


def build_problem(
    data: HaulingMarketData,
    budget: float,
    cargo_m3: float,
    settings: Settings,
    exit_mode: ExitMode = "both",
    hold_days: int = 1,
) -> tuple[Problem, dict[str, tuple[int, str, float]]]:
    """`var_map`: variable name -> `(type_id, kind, unit_price)`, `kind` in
    purchase/instant/listing."""
    is_dest_structure = is_structure_location(data.to_station_id)
    sales_tax = sales_tax_pct(settings.skills, settings.fees)
    broker_pct = broker_fee_pct(
        settings.skills, settings.standings, settings.fees, is_structure=is_dest_structure
    )

    assert exit_mode in EXIT_MODES, f"unknown exit mode {exit_mode!r}"
    assert hold_days >= 1, "hold_days must be at least 1"
    allow_instant = exit_mode in ("instant", "both")
    allow_listing = exit_mode in ("listing", "both")

    def capacity(tid: int) -> float:
        return listing_capacity(data.dest_daily_volume.get(tid, 0.0), settings, hold_days)

    exits: set[int] = set()
    if allow_instant:
        exits |= set(data.dest_instant_levels)
    if allow_listing:
        exits |= {tid for tid in data.dest_listing_price if capacity(tid) > 0}
    tradable_types = set(data.source_sell_levels) & exits

    variables: list[Variable] = []
    obj_coeffs: dict[str, float] = {}
    var_map: dict[str, tuple[int, str, float]] = {}
    purchase_cost_coeffs: dict[str, float] = {}
    cargo_coeffs: dict[str, float] = {}
    balance_coeffs: dict[int, dict[str, float]] = defaultdict(dict)

    for type_id in sorted(tradable_types):
        instant_levels = data.dest_instant_levels.get(type_id, []) if allow_instant else []
        listing_revenue = 0.0
        listing_effective = 0.0
        listing_cap = 0.0
        if allow_listing and type_id in data.dest_listing_price and capacity(type_id) > 0:
            listing_effective = listing_effective_price(
                data.dest_listing_price[type_id],
                data.dest_volatility_pct.get(type_id, 0.0),
                settings,
                hold_days,
            )
            listing_revenue = listing_effective * (1 - sales_tax / 100 - broker_pct / 100)
            listing_cap = capacity(type_id)

        # Exact pruning (never changes the optimum, only shrinks the model): a unit bought at
        # or above the best net revenue any exit can pay is never worth it, and buying more
        # than the exits can absorb in total can't be sold (balance constraint).
        best_unit_revenue = max(
            [lv.price * (1 - sales_tax / 100) for lv in instant_levels[:1]] + [listing_revenue]
        )
        source_levels = [
            lv for lv in data.source_sell_levels[type_id] if lv.price < best_unit_revenue
        ]
        if not source_levels:
            continue
        cheapest = source_levels[0].price
        instant_levels = [
            lv for lv in instant_levels if lv.price * (1 - sales_tax / 100) > cheapest
        ]
        exit_capacity = sum(lv.volume_remain for lv in instant_levels) + (
            listing_cap if listing_revenue > cheapest else 0.0
        )
        kept: list = []
        cumulative = 0
        for lv in source_levels:
            kept.append(lv)
            cumulative += lv.volume_remain
            if cumulative >= exit_capacity:
                break
        source_levels = kept

        integer = cheapest > settings.hauling.integer_price_threshold_isk
        pv = data.packaged_volume[type_id]

        for level in source_levels:
            name = f"x_{level.order_id}"
            variables.append(Variable(name, upper=float(level.volume_remain), integer=integer))
            obj_coeffs[name] = -level.price
            var_map[name] = (type_id, "purchase", level.price)
            purchase_cost_coeffs[name] = level.price
            cargo_coeffs[name] = pv
            balance_coeffs[type_id][name] = -1.0

        for level in instant_levels:
            name = f"y_{level.order_id}"
            revenue = level.price * (1 - sales_tax / 100)
            variables.append(Variable(name, upper=float(level.volume_remain), integer=integer))
            obj_coeffs[name] = revenue
            var_map[name] = (type_id, "instant", level.price)
            balance_coeffs[type_id][name] = 1.0

        if listing_revenue > cheapest:
            name = f"z_{type_id}"
            variables.append(Variable(name, upper=listing_cap, integer=integer))
            obj_coeffs[name] = listing_revenue
            var_map[name] = (type_id, "listing", listing_effective)
            balance_coeffs[type_id][name] = 1.0

    constraints = [
        Constraint("budget", dict(purchase_cost_coeffs), "<=", budget),
        Constraint("cargo", dict(cargo_coeffs), "<=", cargo_m3),
    ]
    if settings.hauling.max_cargo_value_isk > 0:
        constraints.append(
            Constraint(
                "max_cargo_value",
                dict(purchase_cost_coeffs),
                "<=",
                settings.hauling.max_cargo_value_isk,
            )
        )
    for type_id, coeffs in balance_coeffs.items():
        constraints.append(Constraint(f"balance_{type_id}", coeffs, "<=", 0.0))

    problem = Problem(variables, Objective(obj_coeffs, "max"), constraints)
    return problem, var_map


def jumps_between(store: DuckDBStore, from_station_id: int, to_station_id: int) -> int:
    station_system = dict(store.station_locations())
    from_system = station_system.get(from_station_id)
    to_system = station_system.get(to_station_id)
    assert from_system is not None, f"unknown station {from_station_id}"
    assert to_system is not None, f"unknown station {to_station_id}"
    if from_system == to_system:
        return 0
    distances = bfs_within(store.jump_graph(), from_system, max_hops=10_000)
    assert to_system in distances, f"no route between systems {from_system} and {to_system}"
    return distances[to_system]


def profit_per_hour(net_profit: float, jumps: int, time_per_jump_minutes: float) -> float:
    # max(jumps, 1) avoids a division by zero for a same-system station pair -- docking and
    # undocking still take some non-zero time even with no gate travel involved.
    hours = max(jumps, 1) * time_per_jump_minutes / 60
    return net_profit / hours


def _profit_per_day(
    net_profit: float, jumps: int, time_per_jump_minutes: float, hold_days: int
) -> float:
    """Profit per day of the whole cycle: travel time plus days waiting on listings."""
    travel_days = max(jumps, 1) * time_per_jump_minutes / (60 * 24)
    return net_profit / (travel_days + hold_days)


def _pct(numerator: float, denominator: float) -> float:
    return numerator / denominator * 100 if denominator > _EPS else 0.0


@dataclass
class _ItemAgg:
    purchase_qty: float = 0.0
    investment: float = 0.0
    max_buy_price: float = 0.0
    instant_qty: float = 0.0
    instant_revenue: float = 0.0
    listing_qty: float = 0.0
    listing_revenue: float = 0.0
    net: float = 0.0  # this item's contribution to the (fee-adjusted) objective


def solve_hauling(
    store: DuckDBStore,
    from_station_id: int,
    to_station_id: int,
    budget: float,
    cargo_m3: float,
    settings: Settings,
    exit_mode: ExitMode = "both",
    hold_days: int | None = None,
) -> HaulingPlan:
    assert from_station_id != to_station_id, "hauling requires two distinct stations"
    hold_days = settings.hauling.default_hold_days if hold_days is None else hold_days

    data = load_hauling_market_data(store, from_station_id, to_station_id, settings)
    problem, var_map = build_problem(data, budget, cargo_m3, settings, exit_mode, hold_days)
    jumps = jumps_between(store, from_station_id, to_station_id)
    profit_hr = profit_per_hour(0.0, jumps, settings.hauling.time_per_jump_minutes)

    if not problem.variables:
        return HaulingPlan(
            from_station_id,
            to_station_id,
            [],
            0.0,
            0.0,
            0.0,
            jumps,
            profit_hr,
            exit_mode=exit_mode,
            hold_days=hold_days,
        )

    solution = solve(problem)
    assert solution.status == "Optimal", f"hauling MILP not optimal: {solution.status}"

    agg: dict[int, _ItemAgg] = defaultdict(_ItemAgg)
    for name, value in solution.values.items():
        if value <= _EPS:
            continue
        type_id, kind, price = var_map[name]
        item = agg[type_id]
        item.net += problem.objective.coeffs.get(name, 0.0) * value
        if kind == "purchase":
            item.purchase_qty += value
            item.investment += value * price
            item.max_buy_price = max(item.max_buy_price, price)
        elif kind == "instant":
            item.instant_qty += value
            item.instant_revenue += value * price
        else:
            item.listing_qty += value
            item.listing_revenue += value * price

    lines: list[ShoppingListLine] = []
    total_investment = 0.0
    cargo_m3_used = 0.0
    net_profit = 0.0
    gross_revenue = 0.0
    for type_id, item in agg.items():
        # A whole unit is the smallest thing that can actually be bought; the LP relaxation
        # for cheap items can leave sub-unit dust that would render as "qty 0".
        if item.purchase_qty < 1.0:
            continue
        total_investment += item.investment
        cargo_m3_used += item.purchase_qty * data.packaged_volume[type_id]
        net_profit += item.net
        sold_qty = item.instant_qty + item.listing_qty
        revenue = item.instant_revenue + item.listing_revenue
        gross_revenue += revenue
        if item.instant_qty > _EPS and item.listing_qty > _EPS:
            exit_mode = "mixed"
        elif item.listing_qty > _EPS:
            exit_mode = "listing"
        else:
            exit_mode = "instant"
        daily_volume = data.dest_daily_volume.get(type_id)
        lines.append(
            ShoppingListLine(
                type_id=type_id,
                type_name=data.type_name.get(type_id, str(type_id)),
                quantity=item.purchase_qty,
                max_buy_price=item.max_buy_price,
                exit_mode=exit_mode,
                target_sell_price=(revenue / sold_qty) if sold_qty > _EPS else 0.0,
                net_profit=item.net,
                roi_pct=_pct(item.net, item.investment),
                margin_pct=_pct(item.net, revenue),
                daily_volume=daily_volume,
                daily_volume_isk=(
                    daily_volume * data.dest_avg_price.get(type_id, 0.0)
                    if daily_volume is not None
                    else None
                ),
                share_of_daily_volume_pct=(
                    _pct(item.purchase_qty, daily_volume) if daily_volume else None
                ),
            )
        )

    assert total_investment <= budget + _tol(budget), "plan exceeds budget"
    assert cargo_m3_used <= cargo_m3 + _tol(cargo_m3), "plan exceeds cargo capacity"
    if settings.hauling.max_cargo_value_isk > 0:
        max_value = settings.hauling.max_cargo_value_isk
        assert total_investment <= max_value + _tol(max_value), "plan exceeds max cargo value"
    for var in problem.variables:
        value = solution.values.get(var.name, 0.0)
        assert value >= var.lower - _tol(var.lower), f"{var.name} below its lower bound"
        if var.upper is not None:
            assert value <= var.upper + _tol(var.upper), (
                f"{var.name} exceeds its available liquidity"
            )
    for item in agg.values():
        sold = item.instant_qty + item.listing_qty
        assert item.purchase_qty + _tol(sold) >= sold, "sold more than purchased"

    return HaulingPlan(
        from_station_id=from_station_id,
        to_station_id=to_station_id,
        lines=lines,
        total_investment=total_investment,
        cargo_m3_used=cargo_m3_used,
        net_profit=net_profit,
        jumps=jumps,
        profit_per_hour=profit_per_hour(net_profit, jumps, settings.hauling.time_per_jump_minutes),
        roi_pct=_pct(net_profit, total_investment),
        margin_pct=_pct(net_profit, gross_revenue),
        exit_mode=exit_mode,
        hold_days=hold_days,
        profit_per_day_incl_hold=_profit_per_day(
            net_profit,
            jumps,
            settings.hauling.time_per_jump_minutes,
            hold_days if any(line.exit_mode != "instant" for line in lines) else 0,
        ),
    )


def rank_station_pairs(
    store: DuckDBStore,
    station_pairs: list[StationPair],
    budget: float,
    cargo_m3: float,
    settings: Settings,
    exit_mode: ExitMode = "both",
    hold_days: int | None = None,
) -> list[HaulingPlan]:
    plans = [
        solve_hauling(
            store,
            pair.from_station_id,
            pair.to_station_id,
            budget,
            cargo_m3,
            settings,
            exit_mode,
            hold_days,
        )
        for pair in station_pairs
    ]
    return sorted(plans, key=lambda plan: plan.profit_per_hour, reverse=True)
