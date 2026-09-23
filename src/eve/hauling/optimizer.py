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

from collections import defaultdict
from dataclasses import dataclass

from eve.config import Settings, StationPair
from eve.features.jump_graph import bfs_within
from eve.fees.fees import broker_fee_pct, is_structure_location, sales_tax_pct
from eve.hauling.data import HaulingMarketData, load_hauling_market_data
from eve.optimize.problem import Constraint, Objective, Problem, Variable
from eve.optimize.solver import solve
from eve.store.duckdb_store import DuckDBStore

_EPS = 1e-6


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


def build_problem(
    data: HaulingMarketData, budget: float, cargo_m3: float, settings: Settings
) -> tuple[Problem, dict[str, tuple[int, str, float]]]:
    """`var_map`: variable name -> `(type_id, kind, unit_price)`, `kind` in
    purchase/instant/listing."""
    is_dest_structure = is_structure_location(data.to_station_id)
    sales_tax = sales_tax_pct(settings.skills, settings.fees)
    broker_pct = broker_fee_pct(
        settings.skills, settings.standings, settings.fees, is_structure=is_dest_structure
    )

    tradable_types = set(data.source_sell_levels) & (
        set(data.dest_instant_levels)
        | {tid for tid in data.dest_listing_price if data.dest_listing_capacity.get(tid, 0.0) > 0}
    )

    variables: list[Variable] = []
    obj_coeffs: dict[str, float] = {}
    var_map: dict[str, tuple[int, str, float]] = {}
    purchase_cost_coeffs: dict[str, float] = {}
    cargo_coeffs: dict[str, float] = {}
    balance_coeffs: dict[int, dict[str, float]] = defaultdict(dict)

    for type_id in sorted(tradable_types):
        cheapest = min(lv.price for lv in data.source_sell_levels[type_id])
        integer = cheapest > settings.hauling.integer_price_threshold_isk
        pv = data.packaged_volume[type_id]

        for level in data.source_sell_levels[type_id]:
            name = f"x_{level.order_id}"
            variables.append(Variable(name, upper=float(level.volume_remain), integer=integer))
            obj_coeffs[name] = -level.price
            var_map[name] = (type_id, "purchase", level.price)
            purchase_cost_coeffs[name] = level.price
            cargo_coeffs[name] = pv
            balance_coeffs[type_id][name] = -1.0

        for level in data.dest_instant_levels.get(type_id, []):
            name = f"y_{level.order_id}"
            revenue = level.price * (1 - sales_tax / 100)
            variables.append(Variable(name, upper=float(level.volume_remain), integer=integer))
            obj_coeffs[name] = revenue
            var_map[name] = (type_id, "instant", level.price)
            balance_coeffs[type_id][name] = 1.0

        if type_id in data.dest_listing_price and data.dest_listing_capacity.get(type_id, 0.0) > 0:
            ref_price = data.dest_listing_price[type_id]
            effective_price = ref_price * (1 - settings.hauling.listing_time_discount_pct / 100)
            revenue = effective_price * (1 - sales_tax / 100 - broker_pct / 100)
            name = f"z_{type_id}"
            variables.append(
                Variable(name, upper=data.dest_listing_capacity[type_id], integer=integer)
            )
            obj_coeffs[name] = revenue
            var_map[name] = (type_id, "listing", effective_price)
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
) -> HaulingPlan:
    assert from_station_id != to_station_id, "hauling requires two distinct stations"

    data = load_hauling_market_data(store, from_station_id, to_station_id, settings)
    problem, var_map = build_problem(data, budget, cargo_m3, settings)
    jumps = jumps_between(store, from_station_id, to_station_id)
    profit_hr = profit_per_hour(0.0, jumps, settings.hauling.time_per_jump_minutes)

    if not problem.variables:
        return HaulingPlan(from_station_id, to_station_id, [], 0.0, 0.0, 0.0, jumps, profit_hr)

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
            )
        )

    assert total_investment <= budget + _EPS, "plan exceeds budget"
    assert cargo_m3_used <= cargo_m3 + _EPS, "plan exceeds cargo capacity"
    if settings.hauling.max_cargo_value_isk > 0:
        assert total_investment <= settings.hauling.max_cargo_value_isk + _EPS, (
            "plan exceeds max cargo value"
        )
    for var in problem.variables:
        value = solution.values.get(var.name, 0.0)
        assert value >= var.lower - _EPS, f"{var.name} below its lower bound"
        if var.upper is not None:
            assert value <= var.upper + _EPS, f"{var.name} exceeds its available liquidity"
    for item in agg.values():
        assert item.purchase_qty + _EPS >= item.instant_qty + item.listing_qty, (
            "sold more than purchased"
        )

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
    )


def rank_station_pairs(
    store: DuckDBStore,
    station_pairs: list[StationPair],
    budget: float,
    cargo_m3: float,
    settings: Settings,
) -> list[HaulingPlan]:
    plans = [
        solve_hauling(store, pair.from_station_id, pair.to_station_id, budget, cargo_m3, settings)
        for pair in station_pairs
    ]
    return sorted(plans, key=lambda plan: plan.profit_per_hour, reverse=True)
