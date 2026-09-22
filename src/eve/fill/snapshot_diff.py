"""Diffs two consecutive raw order-book snapshots into fill/relist/cancel events.

Only orders present in the *older* snapshot produce an event -- a brand-new order (present
only in the newer snapshot) is informational, not a diff event, and is picked up naturally
the next time it changes or disappears.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

from eve.config import Fill

FILL = "fill"
RELIST = "relist"
CANCEL = "cancel"
GAP_UNKNOWN = "gap_unknown"
UNKNOWN = "unknown"

_COLUMNS = [
    "order_id",
    "region_id",
    "type_id",
    "location_id",
    "is_buy_order",
    "price",
    "volume_remain",
]


def _make_event(
    row: dict,
    from_snapshot_id: str,
    to_snapshot_id: str,
    from_fetched_at: datetime,
    to_fetched_at: datetime,
    event_type: str,
    price: float,
    filled_volume: int | None = None,
) -> dict:
    return {
        "region_id": row["region_id"],
        "location_id": row["location_id"],
        "type_id": row["type_id"],
        "order_id": row["order_id"],
        "is_buy_order": row["is_buy_order"],
        "event_type": event_type,
        "price": price,
        "filled_volume": filled_volume,
        "from_snapshot_id": from_snapshot_id,
        "to_snapshot_id": to_snapshot_id,
        "from_fetched_at": from_fetched_at,
        "to_fetched_at": to_fetched_at,
    }


def diff_snapshots(
    old_df: pl.DataFrame,
    new_df: pl.DataFrame,
    from_snapshot_id: str,
    to_snapshot_id: str,
    from_fetched_at: datetime,
    to_fetched_at: datetime,
    history_volume: dict[int, float],
    settings: Fill,
) -> list[dict]:
    """Diff two order-book snapshots (same region) into a list of per-order diff events."""
    elapsed_hours = (to_fetched_at - from_fetched_at).total_seconds() / 3600
    is_gap = elapsed_hours * 60 > settings.max_gap_minutes

    joined = old_df.select(_COLUMNS).join(
        new_df.select(_COLUMNS), on="order_id", how="full", suffix="_new"
    )

    events: list[dict] = []
    for row in joined.iter_rows(named=True):
        if row["type_id"] is None:
            continue  # brand-new order -- not a diff event

        old_price = row["price"]
        old_volume = row["volume_remain"]

        new_present = row["type_id_new"] is not None
        if new_present:
            new_price = row["price_new"]
            new_volume = row["volume_remain_new"]
            if new_price != old_price:
                events.append(
                    _make_event(
                        row,
                        from_snapshot_id,
                        to_snapshot_id,
                        from_fetched_at,
                        to_fetched_at,
                        RELIST,
                        new_price,
                    )
                )
            if new_price == old_price and new_volume < old_volume:
                events.append(
                    _make_event(
                        row,
                        from_snapshot_id,
                        to_snapshot_id,
                        from_fetched_at,
                        to_fetched_at,
                        FILL,
                        old_price,
                        old_volume - new_volume,
                    )
                )
            continue

        # disappeared -- fill, cancel, or genuinely unknown
        if is_gap:
            events.append(
                _make_event(
                    row,
                    from_snapshot_id,
                    to_snapshot_id,
                    from_fetched_at,
                    to_fetched_at,
                    GAP_UNKNOWN,
                    old_price,
                )
            )
            continue
        daily_volume = history_volume.get(row["type_id"])
        if daily_volume is None:
            events.append(
                _make_event(
                    row,
                    from_snapshot_id,
                    to_snapshot_id,
                    from_fetched_at,
                    to_fetched_at,
                    UNKNOWN,
                    old_price,
                )
            )
            continue
        plausible_capacity = daily_volume * (elapsed_hours / 24) * settings.fill_plausibility_factor
        if old_volume <= plausible_capacity:
            events.append(
                _make_event(
                    row,
                    from_snapshot_id,
                    to_snapshot_id,
                    from_fetched_at,
                    to_fetched_at,
                    FILL,
                    old_price,
                    old_volume,
                )
            )
        else:
            events.append(
                _make_event(
                    row,
                    from_snapshot_id,
                    to_snapshot_id,
                    from_fetched_at,
                    to_fetched_at,
                    CANCEL,
                    old_price,
                )
            )

    return events
