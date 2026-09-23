"""Manual trade log + expected-vs-actual report (Faza 8).

Expected profit is a simplified gross margin -- the saved plan line's suggested
buy/sell prices applied to the trade's ACTUAL quantity, with no fees -- not the plan's own
precisely fee-aware total. The authoritative "expected" figure for a whole run stays
`plan_runs.total_expected_profit` (already fee-aware, untouched here); this is
per-item/per-trade detail for the report.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from eve.store.duckdb_store import DuckDBStore


def log_trade(
    store: DuckDBStore,
    *,
    type_id: int,
    quantity: float,
    buy_price: float,
    sell_price: float,
    run_id: str | None = None,
    notes: str | None = None,
) -> str:
    trade_id = str(uuid.uuid4())
    store.write_trade(
        trade_id, run_id, type_id, datetime.now(UTC), quantity, buy_price, sell_price, notes
    )
    return trade_id


@dataclass(frozen=True)
class TradeComparison:
    trade_id: str
    run_id: str | None
    type_id: int
    type_name: str | None
    quantity: float
    expected_profit: float | None
    actual_profit: float


def _compare(row: tuple) -> TradeComparison:
    (
        trade_id,
        run_id,
        type_id,
        _logged_at,
        quantity,
        actual_buy,
        actual_sell,
        plan_type_name,
        plan_buy,
        plan_sell,
    ) = row
    expected_profit = (plan_sell - plan_buy) * quantity if plan_buy is not None else None
    actual_profit = (actual_sell - actual_buy) * quantity
    return TradeComparison(
        trade_id=trade_id,
        run_id=run_id,
        type_id=type_id,
        type_name=plan_type_name,
        quantity=quantity,
        expected_profit=expected_profit,
        actual_profit=actual_profit,
    )


def report_by_item(store: DuckDBStore, since: datetime | None = None) -> list[dict]:
    by_type: dict[int, dict] = {}
    for c in (_compare(row) for row in store.trades_for_report(since)):
        agg = by_type.setdefault(
            c.type_id,
            {
                "type_id": c.type_id,
                "type_name": c.type_name,
                "expected_profit": 0.0,
                "actual_profit": 0.0,
                "trade_count": 0,
            },
        )
        if c.type_name and not agg["type_name"]:
            agg["type_name"] = c.type_name
        if c.expected_profit is not None:
            agg["expected_profit"] += c.expected_profit
        agg["actual_profit"] += c.actual_profit
        agg["trade_count"] += 1
    return sorted(by_type.values(), key=lambda a: a["type_id"])


def report_by_run(store: DuckDBStore, since: datetime | None = None) -> list[dict]:
    by_run: dict[str | None, dict] = {}
    for c in (_compare(row) for row in store.trades_for_report(since)):
        agg = by_run.setdefault(
            c.run_id,
            {"run_id": c.run_id, "expected_profit": 0.0, "actual_profit": 0.0, "trade_count": 0},
        )
        if c.expected_profit is not None:
            agg["expected_profit"] += c.expected_profit
        agg["actual_profit"] += c.actual_profit
        agg["trade_count"] += 1
    return sorted(by_run.values(), key=lambda a: (a["run_id"] is None, a["run_id"] or ""))
