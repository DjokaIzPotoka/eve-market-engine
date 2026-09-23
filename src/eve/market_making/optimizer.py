"""Market making portfolio allocation (Module A, Faza 7): a 0/1 knapsack over candidate
items, gated by order slots, with continuous capital allocated up to each item's natural
size. Profit scales linearly with allocated capital up to that natural cap (the market's
daily throughput is fixed -- sizing beyond it doesn't add profit/day), so a binary
"selected" variable (consuming 2 order slots: buy + sell) gates a continuous "capital"
variable bounded by the item's `capital_required`.
"""

from __future__ import annotations

from dataclasses import dataclass

from eve.config import Settings
from eve.market_making.candidates import Candidate, load_candidates
from eve.market_making.capital import max_order_slots
from eve.optimize.problem import Constraint, Objective, Problem, Variable
from eve.optimize.solver import solve
from eve.store.duckdb_store import DuckDBStore

_EPS = 1e-6


@dataclass(frozen=True)
class MarketMakingLine:
    type_id: int
    type_name: str
    buy_price: float
    sell_price: float
    capital_allocated: float
    net_profit_per_day: float


@dataclass(frozen=True)
class MarketMakingPlan:
    station_id: int
    lines: list[MarketMakingLine]
    total_capital_used: float
    slots_used: int
    total_net_profit_per_day: float


def build_problem(
    candidates: list[Candidate], capital: float, settings: Settings
) -> tuple[Problem, dict[str, Candidate]]:
    variables: list[Variable] = []
    obj_coeffs: dict[str, float] = {}
    budget_coeffs: dict[str, float] = {}
    slot_coeffs: dict[str, float] = {}
    constraints: list[Constraint] = []
    by_capital_var: dict[str, Candidate] = {}

    for c in candidates:
        selected_name = f"selected_{c.type_id}"
        capital_name = f"capital_{c.type_id}"

        variables.append(Variable(selected_name, upper=1.0, integer=True))
        variables.append(Variable(capital_name, upper=c.capital_required))

        obj_coeffs[capital_name] = c.profit_per_day_per_isk
        budget_coeffs[capital_name] = 1.0
        slot_coeffs[selected_name] = 2.0
        by_capital_var[capital_name] = c

        constraints.append(
            Constraint(
                f"link_{c.type_id}",
                {capital_name: 1.0, selected_name: -c.capital_required},
                "<=",
                0.0,
            )
        )

    constraints.append(Constraint("budget", budget_coeffs, "<=", capital))
    constraints.append(
        Constraint(
            "slots",
            slot_coeffs,
            "<=",
            float(max_order_slots(settings.skills, settings.market_making)),
        )
    )

    problem = Problem(variables, Objective(obj_coeffs, "max"), constraints)
    return problem, by_capital_var


def solve_market_making(
    store: DuckDBStore, station_id: int, capital: float, settings: Settings
) -> MarketMakingPlan:
    candidates = load_candidates(store, station_id, capital, settings)
    if not candidates:
        return MarketMakingPlan(station_id, [], 0.0, 0, 0.0)

    problem, by_capital_var = build_problem(candidates, capital, settings)
    solution = solve(problem)
    assert solution.status == "Optimal", f"market making MILP not optimal: {solution.status}"

    slots = max_order_slots(settings.skills, settings.market_making)

    lines: list[MarketMakingLine] = []
    total_capital_used = 0.0
    slots_used = 0
    total_net_profit_per_day = 0.0
    for name, candidate in by_capital_var.items():
        capital_value = solution.values.get(name, 0.0)
        if capital_value <= _EPS:
            continue
        selected_value = solution.values.get(f"selected_{candidate.type_id}", 0.0)
        assert selected_value > 0.5, f"{candidate.type_id} has capital but is not selected"

        profit = candidate.profit_per_day_per_isk * capital_value
        lines.append(
            MarketMakingLine(
                type_id=candidate.type_id,
                type_name=candidate.type_name,
                buy_price=candidate.buy_price,
                sell_price=candidate.sell_price,
                capital_allocated=capital_value,
                net_profit_per_day=profit,
            )
        )
        total_capital_used += capital_value
        slots_used += 2
        total_net_profit_per_day += profit

    assert total_capital_used <= capital + _EPS, "plan exceeds capital"
    assert slots_used <= slots, "plan exceeds order slots"
    for line in lines:
        candidate = next(c for c in candidates if c.type_id == line.type_id)
        assert line.capital_allocated <= candidate.capital_required + _EPS, (
            "capital allocated exceeds natural order size"
        )
        assert (
            line.capital_allocated
            <= settings.market_making.max_capital_share_per_item * capital + _EPS
        ), "capital allocated exceeds max share per item"

    return MarketMakingPlan(
        station_id=station_id,
        lines=lines,
        total_capital_used=total_capital_used,
        slots_used=slots_used,
        total_net_profit_per_day=total_net_profit_per_day,
    )
