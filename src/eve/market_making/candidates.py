"""Per-item market-making economics for one station (Module A, Faza 7).

Each candidate is a proposed buy (best_bid + tick) / sell (best_ask - tick) order pair,
sized to cover `target_turnover_days` of expected daily flow -- sizing any larger doesn't
raise profit/day (the market's throughput is what it is), just ties up more capital.
"""

from __future__ import annotations

from dataclasses import dataclass

from eve.config import Settings
from eve.fees.fees import (
    broker_fee_isk,
    broker_fee_pct,
    is_structure_location,
    relist_fee_isk,
    sales_tax_pct,
)
from eve.market_making.capital import buy_order_escrow_pct
from eve.store.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class Candidate:
    type_id: int
    type_name: str
    buy_price: float
    sell_price: float
    expected_daily_fill: float
    turnover_days: float
    net_profit_per_day: float
    capital_required: float
    profit_per_day_per_isk: float


def load_diff_metrics(store: DuckDBStore, station_id: int, settings: Settings) -> list[tuple]:
    """Fill/relist rates for `station_id`, averaged over `fill.metrics_window_hours`."""
    return store.latest_book_diff_metrics(
        station_id,
        window_hours=settings.fill.metrics_window_hours,
        max_gap_hours=settings.fill.max_gap_minutes / 60,
    )


def load_candidates(
    store: DuckDBStore, station_id: int, capital: float, settings: Settings
) -> list[Candidate]:
    station_system = dict(store.station_locations())
    system_region = store.system_region_map()
    system_id = station_system.get(station_id)
    region_id = system_region.get(system_id) if system_id is not None else None
    if region_id is None:
        return []

    features = {row[0]: row for row in store.latest_book_features(region_id, station_id)}
    diffs = {row[0]: row for row in load_diff_metrics(store, station_id, settings)}

    mm = settings.market_making
    sales_tax = sales_tax_pct(settings.skills, settings.fees)
    is_structure = is_structure_location(station_id)
    broker_pct = broker_fee_pct(
        settings.skills, settings.standings, settings.fees, is_structure=is_structure
    )
    escrow_pct = buy_order_escrow_pct(settings.skills, mm)

    # `book_features.history_avg_30d` is frozen at snapshot time, so it stays NULL when the
    # history was ingested after the orders; fall back to the current `history` table.
    history_price: dict[int, float] = {}
    snapshot = store.latest_snapshot_meta(region_id)
    if snapshot is not None:
        history_price = {
            tid: price
            for tid, (_vol, _vola, price) in store.history_stats(
                region_id, snapshot.fetched_at.date(), 30
            ).items()
        }

    type_ids = sorted(set(features) & set(diffs))
    type_names = store.types_by_id(type_ids)

    candidates: list[Candidate] = []
    for type_id in type_ids:
        (
            _tid,
            best_bid,
            best_ask,
            bid_near_top,
            ask_near_top,
            _avg_7d,
            avg_30d,
            _deviation,
        ) = features[type_id]
        (
            _tid2,
            bid_volume_per_hour,
            ask_volume_per_hour,
            relists_per_hour,
            _bid_competitors,
            _ask_competitors,
            bid_capture_rate,
            ask_capture_rate,
        ) = diffs[type_id]

        if avg_30d is None:
            avg_30d = history_price.get(type_id)
        if avg_30d is None:
            continue
        if best_bid is None or best_ask is None:
            continue
        if bid_near_top < mm.min_near_top_orders or ask_near_top < mm.min_near_top_orders:
            continue

        buy_price = best_bid + mm.price_tick_isk
        sell_price = best_ask - mm.price_tick_isk
        if buy_price >= sell_price:
            continue
        max_dev = mm.max_price_deviation_pct / 100
        if buy_price < avg_30d * (1 - max_dev) or sell_price > avg_30d * (1 + max_dev):
            continue

        buy_fill_per_day = bid_volume_per_hour * 24 * bid_capture_rate
        sell_fill_per_day = ask_volume_per_hour * 24 * ask_capture_rate
        expected_daily_fill = min(buy_fill_per_day, sell_fill_per_day)
        if expected_daily_fill <= 0:
            continue

        order_size = expected_daily_fill * mm.target_turnover_days
        buy_order_value = buy_price * order_size
        sell_order_value = sell_price * order_size

        gross_profit_per_unit = sell_price * (1 - sales_tax / 100) - buy_price

        broker_cost_per_day = (
            broker_fee_isk(buy_order_value, broker_pct, settings.fees)
            + broker_fee_isk(sell_order_value, broker_pct, settings.fees)
        ) / mm.target_turnover_days
        relist_cost_per_day = (
            relists_per_hour
            * 24
            * (
                relist_fee_isk(
                    buy_order_value,
                    buy_order_value,
                    settings.skills,
                    settings.standings,
                    settings.fees,
                    is_structure=is_structure,
                )
                + relist_fee_isk(
                    sell_order_value,
                    sell_order_value,
                    settings.skills,
                    settings.standings,
                    settings.fees,
                    is_structure=is_structure,
                )
            )
        )

        net_profit_per_day = (
            expected_daily_fill * gross_profit_per_unit - broker_cost_per_day - relist_cost_per_day
        )
        if net_profit_per_day <= 0:
            continue

        capital_required = order_size * (buy_price * escrow_pct / 100 + sell_price)
        if capital_required <= 0:
            continue
        if capital_required > mm.max_capital_share_per_item * capital:
            continue

        name = type_names.get(type_id, (str(type_id), 0.0))[0]
        candidates.append(
            Candidate(
                type_id=type_id,
                type_name=name,
                buy_price=buy_price,
                sell_price=sell_price,
                expected_daily_fill=expected_daily_fill,
                turnover_days=1 / expected_daily_fill,
                net_profit_per_day=net_profit_per_day,
                capital_required=capital_required,
                profit_per_day_per_isk=net_profit_per_day / capital_required,
            )
        )

    return candidates
