"""Tests for diff_snapshots: fill/relist/cancel/gap/unknown classification. No network --
purely derived data, so hand-built synthetic snapshots (not real fixtures) are appropriate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from eve.config import Fill
from eve.fill.snapshot_diff import CANCEL, FILL, GAP_UNKNOWN, RELIST, UNKNOWN, diff_snapshots

REGION_ID = 10000002
LOCATION_ID = 60003760

_SCHEMA = {
    "order_id": pl.Int64,
    "region_id": pl.Int64,
    "type_id": pl.Int64,
    "location_id": pl.Int64,
    "is_buy_order": pl.Boolean,
    "price": pl.Float64,
    "volume_remain": pl.Int64,
}


def _order(
    order_id: int, type_id: int, price: float, volume_remain: int, is_buy: bool = True
) -> dict:
    return {
        "order_id": order_id,
        "region_id": REGION_ID,
        "type_id": type_id,
        "location_id": LOCATION_ID,
        "is_buy_order": is_buy,
        "price": price,
        "volume_remain": volume_remain,
    }


def _df(*orders: dict) -> pl.DataFrame:
    if not orders:
        return pl.DataFrame(schema=_SCHEMA)
    return pl.DataFrame(list(orders), schema=_SCHEMA)


def _diff(
    old_orders: list[dict],
    new_orders: list[dict],
    minutes: int = 10,
    history_volume: dict[int, float] | None = None,
) -> list[dict]:
    from_time = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    to_time = from_time + timedelta(minutes=minutes)
    return diff_snapshots(
        _df(*old_orders),
        _df(*new_orders),
        "snap-old",
        "snap-new",
        from_time,
        to_time,
        history_volume or {},
        Fill(),
    )


def test_volume_decrease_same_price_is_a_fill() -> None:
    events = _diff([_order(1, 34, 5.0, 100)], [_order(1, 34, 5.0, 60)])

    assert len(events) == 1
    assert events[0]["event_type"] == FILL
    assert events[0]["filled_volume"] == 40
    assert events[0]["price"] == 5.0


def test_price_change_is_a_relist() -> None:
    events = _diff([_order(1, 34, 5.0, 100)], [_order(1, 34, 5.5, 100)])

    assert len(events) == 1
    assert events[0]["event_type"] == RELIST
    assert events[0]["price"] == 5.5


def test_disappeared_small_volume_relative_to_history_is_a_fill() -> None:
    # history: 2400 units/day; at 10 min elapsed, plausible capacity ~= 16.7 units
    events = _diff([_order(1, 34, 5.0, 10)], [], minutes=10, history_volume={34: 2400})

    assert len(events) == 1
    assert events[0]["event_type"] == FILL
    assert events[0]["filled_volume"] == 10


def test_disappeared_large_volume_relative_to_history_is_a_cancel() -> None:
    events = _diff([_order(1, 34, 5.0, 10000)], [], minutes=10, history_volume={34: 2400})

    assert len(events) == 1
    assert events[0]["event_type"] == CANCEL


def test_disappeared_with_no_history_is_unknown() -> None:
    events = _diff([_order(1, 34, 5.0, 10)], [], minutes=10, history_volume={})

    assert len(events) == 1
    assert events[0]["event_type"] == UNKNOWN


def test_gap_marks_disappeared_orders_as_gap_unknown_regardless_of_volume() -> None:
    # tiny volume would normally be a slam-dunk fill, but a 3h gap exceeds the default 60 min
    events = _diff([_order(1, 34, 5.0, 1)], [], minutes=180, history_volume={34: 1_000_000})

    assert len(events) == 1
    assert events[0]["event_type"] == GAP_UNKNOWN


def test_persisting_order_across_a_gap_still_detects_fill_and_relist() -> None:
    old = [_order(1, 34, 5.0, 100), _order(2, 34, 6.0, 50)]
    new = [_order(1, 34, 5.0, 60), _order(2, 34, 6.5, 50)]

    events = _diff(old, new, minutes=180)  # gap-sized interval, but both orders persist

    by_order = {e["order_id"]: e["event_type"] for e in events}
    assert by_order[1] == FILL
    assert by_order[2] == RELIST


def test_new_order_produces_no_event() -> None:
    events = _diff([], [_order(1, 34, 5.0, 100)])

    assert events == []
