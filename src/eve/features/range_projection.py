"""Buy-order range projection: which stations a buy order's range actually covers.

Extracted so both `compute_book_features` (Faza 3) and the hauling optimizer (Faza 6) can
project a region's buy orders onto specific stations without duplicating the
station/solarsystem/region/N-jump dispatch logic.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from eve.features.jump_graph import bfs_within
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectedBuyOrder:
    order_id: int
    type_id: int
    location_id: int
    price: float
    volume_remain: int


def project_buy_orders(
    store: DuckDBStore, region_id: int, location_ids: list[int] | None = None
) -> list[ProjectedBuyOrder]:
    """Every buy order in `region_id`, expanded to one row per station its `range` covers.

    An order with a region/solarsystem/N-jump range appears once per covered station (same
    `order_id`, different `location_id`) -- callers that need per-order uniqueness should
    group by `order_id` themselves. `location_ids` restricts which stations produce output
    rows without narrowing coverage against the rest of the region (a region-range order at
    C still counts toward B even if only B was requested).
    """
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

    graph: dict[int, set[int]] | None = None
    bfs_cache: dict[tuple[int, int], dict[int, int]] = {}
    unknown_ranges: set[str] = set()
    rows: list[ProjectedBuyOrder] = []

    for (
        order_id,
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
            rows.append(
                ProjectedBuyOrder(
                    order_id=order_id,
                    type_id=type_id,
                    location_id=loc,
                    price=price,
                    volume_remain=volume_remain,
                )
            )

    if unknown_ranges:
        logger.warning("unrecognized order range value(s), orders skipped: %s", unknown_ranges)

    return rows
