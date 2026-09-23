"""Tests for hauling exit modes (instant / listing / both) and listing hold days."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Features,
    Fees,
    Fill,
    Filters,
    Hauling,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.fees.fees import broker_fee_pct, sales_tax_pct
from eve.hauling.optimizer import HaulingPlan, solve_hauling
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 10000090
SYS_A, SYS_B = 30000001, 30000002
STATION_A, STATION_B = 60000201, 60000202
FETCHED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=STATION_A, to_station_id=STATION_B)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path="unused"),
        features=Features(),
        fill=Fill(),
        fees=Fees(),
        hauling=Hauling(),
    )


def _order(
    store: DuckDBStore,
    order_id: int,
    *,
    location_id: int,
    system_id: int,
    is_buy: bool,
    price: float,
    volume: int,
    type_id: int,
) -> None:
    store._con.execute(
        """
        INSERT INTO orders_latest
        (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
         volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 90, 'station', ?, 'snap-1', ?)
        """,
        [
            order_id,
            type_id,
            location_id,
            system_id,
            is_buy,
            price,
            volume,
            volume,
            REGION_ID,
            FETCHED_AT.replace(tzinfo=None),
        ],
    )


def _mode_store(tmp_path: Path) -> DuckDBStore:
    store = DuckDBStore(tmp_path / "eve.duckdb")
    for system_id, station_id in ((SYS_A, STATION_A), (SYS_B, STATION_B)):
        store._con.execute(
            "INSERT INTO solar_systems VALUES (?, ?, 1, 0.5, ?)",
            [system_id, REGION_ID, f"sys-{system_id}"],
        )
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)",
            [station_id, f"station-{station_id}", system_id],
        )
    store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [SYS_A, SYS_B])
    store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [SYS_B, SYS_A])
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, 1)",
        [
            "snap-1",
            REGION_ID,
            FETCHED_AT.replace(tzinfo=None),
            (FETCHED_AT + timedelta(minutes=5)).replace(tzinfo=None),
        ],
    )
    return store


def _seed_market(
    store: DuckDBStore,
    type_id: int,
    *,
    daily_volume: int,
    high: float = 10.0,
    low: float = 10.0,
    instant_qty: int = 100,
    source_qty: int = 1000,
) -> None:
    """Source ask 1.0; destination: an instant buy order at 4.0 (`instant_qty` units), a
    resting sell order at 10.0 (the listing reference price) and one day of history."""
    store._con.execute(
        "INSERT INTO types VALUES (?, ?, 0.01, 0, TRUE)", [type_id, f"type-{type_id}"]
    )
    base = type_id * 10
    _order(
        store, base + 1, location_id=STATION_A, system_id=SYS_A, is_buy=False,
        price=1.0, volume=source_qty, type_id=type_id,
    )  # fmt: skip
    _order(
        store, base + 2, location_id=STATION_B, system_id=SYS_B, is_buy=True,
        price=4.0, volume=instant_qty, type_id=type_id,
    )  # fmt: skip
    _order(
        store, base + 3, location_id=STATION_B, system_id=SYS_B, is_buy=False,
        price=10.0, volume=5, type_id=type_id,
    )  # fmt: skip
    day = FETCHED_AT.date() - timedelta(days=1)
    store.write_history(
        REGION_ID,
        type_id,
        day,
        [
            {
                "date": day.isoformat(),
                "average": 10.0,
                "highest": high,
                "lowest": low,
                "order_count": 1,
                "volume": daily_volume,
            }
        ],
    )


def _solve(store: DuckDBStore, mode: str, hold_days: int = 1) -> HaulingPlan:
    return solve_hauling(
        store,
        STATION_A,
        STATION_B,
        budget=1_000_000.0,
        cargo_m3=100_000.0,
        settings=_settings(),
        exit_mode=mode,  # type: ignore[arg-type]
        hold_days=hold_days,
    )


def test_instant_mode_only_sells_into_buy_orders(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=100_000)  # listing alone could sell far more
        instant = _solve(store, "instant")
        both = _solve(store, "both")

    assert [line.exit_mode for line in instant.lines] == ["instant"]
    assert instant.lines[0].quantity == pytest.approx(100.0)  # the buy order's volume
    assert both.lines[0].quantity > instant.lines[0].quantity
    assert instant.exit_mode == "instant"


def test_listing_mode_ignores_existing_buy_orders(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=10_000)  # capacity 0.1 * 10_000 * 1 = 1000
        listing = _solve(store, "listing")

    assert [line.exit_mode for line in listing.lines] == ["listing"]
    assert listing.lines[0].quantity == pytest.approx(1000.0)


def test_listing_capacity_scales_with_hold_days(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=2000, source_qty=5000)  # 0.1 * 2000 = 200 / day
        qty = {d: _solve(store, "listing", d).lines[0].quantity for d in (1, 3, 7)}

    assert qty[1] == pytest.approx(200.0)
    assert qty[3] == pytest.approx(600.0)
    assert qty[7] == pytest.approx(1400.0)


def test_longer_hold_lowers_profit_per_unit_via_time_discount(tmp_path: Path) -> None:
    settings = _settings()
    tax = sales_tax_pct(settings.skills, settings.fees)
    broker = broker_fee_pct(settings.skills, settings.standings, settings.fees)
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=1_000_000, source_qty=50)
        plans = {d: _solve(store, "listing", d) for d in (1, 7)}

    for days, plan in plans.items():
        effective = 10.0 * (1 - settings.hauling.listing_discount_pct_per_day * days / 100)
        expected = 50 * (effective * (1 - tax / 100 - broker / 100) - 1.0)
        assert plan.net_profit == pytest.approx(expected, rel=1e-6)
    assert plans[7].net_profit < plans[1].net_profit


def test_volatile_item_drops_out_on_long_hold_but_stable_item_stays(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 81, daily_volume=1_000_000, source_qty=50)  # stable
        _seed_market(store, 82, daily_volume=1_000_000, source_qty=50, high=15.0, low=5.0)
        one_day = {line.type_id for line in _solve(store, "listing", 1).lines}
        seven_days = {line.type_id for line in _solve(store, "listing", 7).lines}

    assert one_day == {81, 82}  # a ~51% risk haircut still leaves the volatile item profitable
    assert seven_days == {81}  # sqrt(7) scaling pushes the volatile item below cost


def test_profit_per_day_includes_holding_time_only_for_listing(tmp_path: Path) -> None:
    settings = _settings()
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=1_000_000, source_qty=50)
        listing = _solve(store, "listing", 3)
        instant = _solve(store, "instant", 3)

    travel_days = max(listing.jumps, 1) * settings.hauling.time_per_jump_minutes / (60 * 24)
    assert listing.profit_per_day_incl_hold == pytest.approx(listing.net_profit / (travel_days + 3))
    assert instant.profit_per_day_incl_hold == pytest.approx(instant.net_profit / travel_days)


def test_unknown_exit_mode_is_rejected(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=1000)
        with pytest.raises(AssertionError, match="unknown exit mode"):
            _solve(store, "sideways")


def test_history_stats_averages_volume_and_volatility_within_window(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        before = FETCHED_AT.date()

        def row(days_ago: int, volume: int, high: float, low: float) -> dict:
            day = before - timedelta(days=days_ago)
            return {
                "date": day.isoformat(),
                "average": 10.0,
                "highest": high,
                "lowest": low,
                "order_count": 1,
                "volume": volume,
            }

        store.write_history(
            REGION_ID,
            5,
            before,
            [
                row(1, 100, 12.0, 8.0),  # volatility 40%
                row(2, 300, 15.0, 5.0),  # volatility 100%
                row(30, 99_999, 20.0, 0.0),  # outside a 14-day window
                row(0, 99_999, 20.0, 0.0),  # the snapshot day itself is excluded
            ],
        )
        stats = store.history_stats(REGION_ID, before, 14)

    # volume (100 + 300) / 2; volatility = mean intraday range (70%) + stddev of the daily
    # average prices (both days average 10.0, so 0); avg price 10.0
    assert stats[5] == (pytest.approx(200.0), pytest.approx(70.0), pytest.approx(10.0))


def test_history_stats_counts_day_to_day_price_jumps_as_volatility(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        before = FETCHED_AT.date()
        rows = [
            {
                "date": (before - timedelta(days=d)).isoformat(),
                "average": avg,
                "highest": avg,  # single trade per day: no intraday range at all
                "lowest": avg,
                "order_count": 1,
                "volume": 5,
            }
            for d, avg in ((1, 1_000_000.0), (2, 55_000.0))
        ]
        store.write_history(REGION_ID, 6, before, rows)
        _vol, volatility, avg_price = store.history_stats(REGION_ID, before, 14)[6]

    assert volatility == pytest.approx(89.6, abs=0.1)  # stddev 472,500 / mean 527,500
    assert avg_price == pytest.approx(527_500.0)


def test_listing_price_is_capped_by_recent_average_price(tmp_path: Path) -> None:
    """A lone outlier ask (10.0) above the historical average (4.0) must not be the sale price."""
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=1_000_000, source_qty=50)
        day = FETCHED_AT.date() - timedelta(days=1)
        store.write_history(
            REGION_ID,
            80,
            day,
            [
                {
                    "date": day.isoformat(),
                    "average": 4.0,
                    "highest": 4.0,
                    "lowest": 4.0,
                    "order_count": 1,
                    "volume": 1_000_000,
                }
            ],
        )
        plan = _solve(store, "listing", 1)

    assert plan.lines[0].target_sell_price < 4.0  # <= avg price 4.0 minus the haircuts


def test_lines_report_destination_daily_volume_in_units_isk_and_share(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=2000, source_qty=50)  # avg price 10.0
        plan = _solve(store, "instant")  # 50 units of a 100-unit buy order

    line = plan.lines[0]
    assert line.daily_volume == pytest.approx(2000.0)
    assert line.daily_volume_isk == pytest.approx(2000.0 * 10.0)
    assert line.share_of_daily_volume_pct == pytest.approx(line.quantity / 2000.0 * 100)


def test_daily_volume_is_none_when_destination_has_no_history(tmp_path: Path) -> None:
    with _mode_store(tmp_path) as store:
        _seed_market(store, 80, daily_volume=2000, source_qty=50)
        store._con.execute("DELETE FROM history")  # instant exit doesn't need history
        plan = _solve(store, "instant")

    line = plan.lines[0]
    assert line.daily_volume is None
    assert line.daily_volume_isk is None
    assert line.share_of_daily_volume_pct is None
