"""Persist Module A/B plan outputs (Faza 8) so `eve log-trade`/`eve report` have an
"expected" baseline to compare real trades against. Neither `HaulingPlan` nor
`MarketMakingPlan` is modified -- these just translate the existing dataclasses into rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from eve.config import Settings
from eve.hauling.optimizer import HaulingPlan
from eve.market_making.capital import buy_order_escrow_pct
from eve.market_making.optimizer import MarketMakingPlan
from eve.store.duckdb_store import DuckDBStore


def save_hauling_plan(store: DuckDBStore, plan: HaulingPlan) -> str:
    run_id = str(uuid.uuid4())
    store.write_plan_run(
        run_id,
        "haul",
        datetime.now(UTC),
        plan.from_station_id,
        plan.to_station_id,
        plan.net_profit,
        "per_run",
    )
    store.write_plan_lines(
        run_id,
        [
            {
                "type_id": line.type_id,
                "type_name": line.type_name,
                "suggested_quantity": line.quantity,
                "suggested_buy_price": line.max_buy_price,
                "suggested_sell_price": line.target_sell_price,
            }
            for line in plan.lines
        ],
    )
    return run_id


def save_market_making_plan(store: DuckDBStore, plan: MarketMakingPlan, settings: Settings) -> str:
    run_id = str(uuid.uuid4())
    store.write_plan_run(
        run_id,
        "mm",
        datetime.now(UTC),
        plan.station_id,
        None,
        plan.total_net_profit_per_day,
        "per_day",
    )
    # MarketMakingLine only carries capital_allocated, not a unit count -- invert the
    # same per-unit capital formula optimizer.py used to build it (buy escrow + sell
    # value) to get the implied quantity back.
    escrow_pct = buy_order_escrow_pct(settings.skills, settings.market_making)
    store.write_plan_lines(
        run_id,
        [
            {
                "type_id": line.type_id,
                "type_name": line.type_name,
                "suggested_quantity": line.capital_allocated
                / (line.buy_price * escrow_pct / 100 + line.sell_price),
                "suggested_buy_price": line.buy_price,
                "suggested_sell_price": line.sell_price,
            }
            for line in plan.lines
        ],
    )
    return run_id
