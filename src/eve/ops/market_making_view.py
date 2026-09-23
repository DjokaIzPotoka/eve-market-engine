"""Browsable market-making table for the dashboard: every viable candidate per station with
the columns a trader scans (spread, fills/day, competition, capital), not just the LP's pick.

Reuses `load_candidates` for the economics so the numbers match `eve mm`; this only joins in
the competition metric (`book_diff_metrics.relists_per_hour`), the net spread, and region-wide
daily volume from history.
"""

from __future__ import annotations

from dataclasses import dataclass

from eve.config import Settings
from eve.fees.fees import sales_tax_pct
from eve.market_making.candidates import load_candidates, load_diff_metrics
from eve.store.duckdb_store import DuckDBStore

HISTORY_WINDOW_DAYS = 30


@dataclass(frozen=True)
class MarketMakingRow:
    type_id: int
    type_name: str
    location_id: int
    station_name: str
    suggested_buy: float
    suggested_sell: float
    net_spread_pct: float
    expected_fills_per_day: float
    relists_per_hour: float  # top-of-book changes per hour: the competition signal
    tied_capital: float
    profit_per_day: float
    daily_volume: float  # region-wide average units/day over HISTORY_WINDOW_DAYS
    profit_per_day_per_isk: float


def market_making_rows(
    store: DuckDBStore, station_ids: list[int], capital: float, settings: Settings
) -> list[MarketMakingRow]:
    station_names = dict(store.all_stations())
    station_system = dict(store.station_locations())
    system_region = store.system_region_map()
    tax = sales_tax_pct(settings.skills, settings.fees)

    rows: list[MarketMakingRow] = []
    for station_id in station_ids:
        candidates = load_candidates(store, station_id, capital, settings)
        if not candidates:
            continue
        relists = {row[0]: row[3] for row in load_diff_metrics(store, station_id, settings)}

        region_id = system_region.get(station_system.get(station_id, -1))
        volumes: dict[int, float] = {}
        snapshot = store.latest_snapshot_meta(region_id) if region_id is not None else None
        if region_id is not None and snapshot is not None:
            volumes = {
                type_id: vol
                for type_id, (vol, _vola, _price) in store.history_stats(
                    region_id, snapshot.fetched_at.date(), HISTORY_WINDOW_DAYS
                ).items()
            }

        name = station_names.get(station_id, str(station_id)).split(" - ")[0]
        for c in candidates:
            rows.append(
                MarketMakingRow(
                    type_id=c.type_id,
                    type_name=c.type_name,
                    location_id=station_id,
                    station_name=name,
                    suggested_buy=c.buy_price,
                    suggested_sell=c.sell_price,
                    net_spread_pct=(c.sell_price * (1 - tax / 100) - c.buy_price)
                    / c.buy_price
                    * 100,
                    expected_fills_per_day=c.expected_daily_fill,
                    relists_per_hour=relists.get(c.type_id) or 0.0,
                    tied_capital=c.capital_required,
                    profit_per_day=c.net_profit_per_day,
                    daily_volume=volumes.get(c.type_id, 0.0),
                    profit_per_day_per_isk=c.profit_per_day_per_isk,
                )
            )
    return sorted(rows, key=lambda r: r.profit_per_day_per_isk, reverse=True)
