"""DuckDB-scoped market data for one hauling station pair (Module B, Faza 6)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from eve.config import Settings
from eve.features.range_projection import project_buy_orders
from eve.store.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class BookLevel:
    order_id: int
    price: float
    volume_remain: int


@dataclass(frozen=True)
class HaulingMarketData:
    from_station_id: int
    to_station_id: int
    source_sell_levels: dict[int, list[BookLevel]]  # type_id -> source sell-book rows
    dest_instant_levels: dict[int, list[BookLevel]]  # type_id -> dest covering buy orders
    dest_listing_price: dict[int, float]  # type_id -> dest best ask (listing reference)
    dest_listing_capacity: dict[int, float]  # type_id -> daily-volume-share cap, units
    packaged_volume: dict[int, float]  # type_id -> m3/unit
    type_name: dict[int, str]


def load_hauling_market_data(
    store: DuckDBStore, from_station_id: int, to_station_id: int, settings: Settings
) -> HaulingMarketData:
    """Everything the optimizer needs for one `(from_station_id, to_station_id)` run.

    Items with no known `packaged_volume` (never seen in the SDE) are dropped entirely --
    the cargo constraint can't be enforced for them, so they can't safely be offered.
    """
    station_system = dict(store.station_locations())
    system_region = store.system_region_map()
    dest_system = station_system.get(to_station_id)
    dest_region_id = system_region.get(dest_system) if dest_system is not None else None

    source_sell_levels: dict[int, list[BookLevel]] = defaultdict(list)
    for order_id, type_id, price, volume_remain in store.sell_orders_at_location(from_station_id):
        source_sell_levels[type_id].append(BookLevel(order_id, price, volume_remain))
    for levels in source_sell_levels.values():
        levels.sort(key=lambda lv: lv.price)

    dest_instant_levels: dict[int, list[BookLevel]] = defaultdict(list)
    if dest_region_id is not None:
        for po in project_buy_orders(store, dest_region_id, location_ids=[to_station_id]):
            dest_instant_levels[po.type_id].append(
                BookLevel(po.order_id, po.price, po.volume_remain)
            )
    for levels in dest_instant_levels.values():
        levels.sort(key=lambda lv: lv.price, reverse=True)

    dest_listing_price: dict[int, float] = {}
    for _order_id, type_id, price, _volume_remain in store.sell_orders_at_location(to_station_id):
        current = dest_listing_price.get(type_id)
        if current is None or price < current:
            dest_listing_price[type_id] = price

    dest_listing_capacity: dict[int, float] = {}
    if dest_region_id is not None:
        snapshot = store.latest_snapshot_meta(dest_region_id)
        if snapshot is not None:
            target_date = snapshot.fetched_at.date() - timedelta(days=1)
            volumes = store.history_volume_map(dest_region_id, target_date)
            share = settings.hauling.listing_daily_volume_share
            dest_listing_capacity = {type_id: vol * share for type_id, vol in volumes.items()}

    all_type_ids = set(source_sell_levels) | set(dest_instant_levels) | set(dest_listing_price)
    type_ref = store.types_by_id(list(all_type_ids))

    known_type_ids = all_type_ids & set(type_ref)
    packaged_volume = {tid: type_ref[tid][1] for tid in known_type_ids}
    type_name = {tid: type_ref[tid][0] for tid in known_type_ids}

    return HaulingMarketData(
        from_station_id=from_station_id,
        to_station_id=to_station_id,
        source_sell_levels={
            tid: levels for tid, levels in source_sell_levels.items() if tid in known_type_ids
        },
        dest_instant_levels={
            tid: levels for tid, levels in dest_instant_levels.items() if tid in known_type_ids
        },
        dest_listing_price={
            tid: p for tid, p in dest_listing_price.items() if tid in known_type_ids
        },
        dest_listing_capacity={
            tid: cap for tid, cap in dest_listing_capacity.items() if tid in known_type_ids
        },
        packaged_volume=packaged_volume,
        type_name=type_name,
    )
