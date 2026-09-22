"""Per-(location, item) order-book aggregates, appended per snapshot.

Buy orders with `min_volume > 1` are excluded entirely (CLAUDE.md's exit-liquidity filter).
Sell orders are always local to their `location_id` -- the `range` field is only meaningful
for buy orders (a sell listing can only ever be filled by traveling to that exact station).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from eve.config import Settings
from eve.features.jump_graph import bfs_within
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


def _depth_levels(
    entries: list[tuple[float, int]], depth_levels: int, *, descending: bool
) -> list[int]:
    """Volume at each of the `depth_levels` best distinct prices (not cumulative)."""
    by_price: dict[float, int] = defaultdict(int)
    for price, volume in entries:
        by_price[price] += volume
    best_prices = sorted(by_price, reverse=descending)[:depth_levels]
    return [by_price[p] for p in best_prices]


def _near_top_count(
    entries: list[tuple[float, int]], best_price: float, pct: float, *, is_ask: bool
) -> int:
    if is_ask:
        return sum(1 for price, _ in entries if price <= best_price * (1 + pct))
    return sum(1 for price, _ in entries if price >= best_price * (1 - pct))


def compute_book_features(
    store: DuckDBStore,
    region_id: int,
    settings: Settings,
    location_ids: list[int] | None = None,
) -> int:
    """Compute and store `book_features` for `region_id`'s current `orders_latest` snapshot.

    `location_ids` restricts which stations get an output row -- coverage is still computed
    against the *whole* region (a region-range order elsewhere still counts), only the rows
    materialized at the end are filtered. This matters at real-world scale: a liquid region
    like The Forge has ~350 stations and region-range buy orders for nearly every traded
    type, so an unfiltered run produces millions of rows most callers never look at (Module A
    only ever cares about the one station it's operating from). Pass `None` for the full
    region (documented as slow on a big/liquid region, kept for completeness).
    """
    snapshot = store.latest_snapshot_meta(region_id)
    if snapshot is None:
        logger.warning("no snapshot for region %d, nothing to compute", region_id)
        return 0

    target_locations = set(location_ids) if location_ids is not None else None

    station_system = dict(store.station_locations())
    system_region = store.system_region_map()
    region_stations = [
        loc
        for loc, sys in station_system.items()
        if system_region.get(sys) == region_id
        and (target_locations is None or loc in target_locations)
    ]
    stations_by_system: dict[int, list[int]] = defaultdict(list)
    for loc in region_stations:
        stations_by_system[station_system[loc]].append(loc)

    sell_agg: dict[tuple[int, int], list[tuple[float, int]]] = defaultdict(list)
    for location_id, type_id, price, volume_remain in store.sell_orders(region_id):
        if target_locations is not None and location_id not in target_locations:
            continue
        sell_agg[(location_id, type_id)].append((price, volume_remain))

    buy_agg: dict[tuple[int, int], list[tuple[float, int]]] = defaultdict(list)
    graph: dict[int, set[int]] | None = None
    bfs_cache: dict[tuple[int, int], dict[int, int]] = {}
    unknown_ranges: set[str] = set()

    for (
        _order_id,
        type_id,
        location_id,
        system_id,
        price,
        volume_remain,
        range_,
    ) in store.buy_orders_for_projection(region_id):
        if range_ == "station":
            covered = [location_id]
        elif range_ == "solarsystem":
            covered = stations_by_system.get(system_id, [])
        elif range_ == "region":
            covered = region_stations
        elif range_.isdigit():
            max_hops = int(range_)
            if graph is None:
                graph = store.jump_graph()
            cache_key = (system_id, max_hops)
            reachable = bfs_cache.get(cache_key)
            if reachable is None:
                reachable = bfs_within(graph, system_id, max_hops)
                bfs_cache[cache_key] = reachable
            covered = [loc for sys in reachable for loc in stations_by_system.get(sys, [])]
        else:
            unknown_ranges.add(range_)
            continue

        if target_locations is not None:
            covered = [loc for loc in covered if loc in target_locations]

        for loc in covered:
            buy_agg[(loc, type_id)].append((price, volume_remain))

    if unknown_ranges:
        logger.warning("unrecognized order range value(s), orders skipped: %s", unknown_ranges)

    as_of = snapshot.fetched_at.date()
    history = store.history_averages(region_id, as_of)
    depth_levels = settings.features.depth_levels
    near_top_pct = settings.features.near_top_pct

    rows = []
    for location_id, type_id in set(sell_agg) | set(buy_agg):
        asks = sell_agg.get((location_id, type_id), [])
        bids = buy_agg.get((location_id, type_id), [])

        best_ask = min((p for p, _ in asks), default=None)
        best_bid = max((p for p, _ in bids), default=None)

        ask_depth = _depth_levels(asks, depth_levels, descending=False) if asks else []
        bid_depth = _depth_levels(bids, depth_levels, descending=True) if bids else []

        ask_near_top = (
            _near_top_count(asks, best_ask, near_top_pct, is_ask=True)
            if best_ask is not None
            else 0
        )
        bid_near_top = (
            _near_top_count(bids, best_bid, near_top_pct, is_ask=False)
            if best_bid is not None
            else 0
        )

        avg_7d, avg_30d = history.get(type_id, (None, None))
        mid_price = _mid_price(best_bid, best_ask)
        deviation = None
        if mid_price is not None and avg_30d:
            deviation = (mid_price - avg_30d) / avg_30d * 100

        rows.append(
            {
                "location_id": location_id,
                "type_id": type_id,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_depth_volumes": bid_depth,
                "ask_depth_volumes": ask_depth,
                "bid_orders_near_top": bid_near_top,
                "ask_orders_near_top": ask_near_top,
                "history_avg_7d": avg_7d,
                "history_avg_30d": avg_30d,
                "price_deviation_30d_pct": deviation,
            }
        )

    store.write_book_features(snapshot.snapshot_id, rows)
    logger.info("computed book_features for region %d: %d rows", region_id, len(rows))
    return len(rows)


def _mid_price(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2
    return best_ask if best_ask is not None else best_bid
