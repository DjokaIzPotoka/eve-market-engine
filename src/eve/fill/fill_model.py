"""Aggregates snapshot diff events into per-(location,type) fill-rate and competition metrics."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from eve.config import Settings
from eve.fill.snapshot_diff import FILL, RELIST, diff_snapshots
from eve.store.duckdb_store import DuckDBStore, SnapshotMeta

logger = logging.getLogger(__name__)


def estimate_capture_rate(competitor_count: int) -> float:
    """Naive equal-split model: you plus `competitor_count` others share the flow evenly."""
    return 1.0 / (competitor_count + 1)


def _best_prices(
    df: pl.DataFrame,
) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    """`(location_id, type_id) -> best price`, separately for bids (max) and asks (min)."""
    bids = (
        df.filter(pl.col("is_buy_order"))
        .group_by(["location_id", "type_id"])
        .agg(pl.col("price").max().alias("best"))
    )
    asks = (
        df.filter(~pl.col("is_buy_order"))
        .group_by(["location_id", "type_id"])
        .agg(pl.col("price").min().alias("best"))
    )
    best_bid = {(r["location_id"], r["type_id"]): r["best"] for r in bids.iter_rows(named=True)}
    best_ask = {(r["location_id"], r["type_id"]): r["best"] for r in asks.iter_rows(named=True)}
    return best_bid, best_ask


def _competitor_counts(
    df: pl.DataFrame,
    best_bid: dict[tuple[int, int], float],
    best_ask: dict[tuple[int, int], float],
    near_top_pct: float,
) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], int]]:
    """`(location_id, type_id) -> order count within `near_top_pct` of best`, per side."""
    bid_counts: dict[tuple[int, int], int] = defaultdict(int)
    ask_counts: dict[tuple[int, int], int] = defaultdict(int)
    for row in df.iter_rows(named=True):
        key = (row["location_id"], row["type_id"])
        if row["is_buy_order"]:
            best = best_bid.get(key)
            if best is not None and row["price"] >= best * (1 - near_top_pct):
                bid_counts[key] += 1
        else:
            best = best_ask.get(key)
            if best is not None and row["price"] <= best * (1 + near_top_pct):
                ask_counts[key] += 1
    return bid_counts, ask_counts


def compute_book_metrics(
    events: list[dict],
    old_df: pl.DataFrame,
    new_df: pl.DataFrame,
    from_snapshot_id: str,
    to_snapshot_id: str,
    from_fetched_at: datetime,
    to_fetched_at: datetime,
    near_top_pct: float,
) -> list[dict]:
    """Turn diff events into per-(location,type) bid/ask rate metrics for this interval.

    Scoped to just the (location, type) pairs that actually had a diff event -- computing
    competitor counts etc. across the *whole* snapshot would repeat Faza 3's full-region
    blow-up for no benefit (nothing changed at the other pairs during this interval).
    """
    elapsed_hours = (to_fetched_at - from_fetched_at).total_seconds() / 3600
    if elapsed_hours <= 0 or not events:
        return []

    relevant_keys = pl.DataFrame(
        {
            "location_id": [e["location_id"] for e in events],
            "type_id": [e["type_id"] for e in events],
        }
    ).unique()

    # Select down to just what's needed *before* joining/iterating -- the full raw frame
    # carries tz-aware Datetime columns (issued, fetched_at) that crash polars' row iteration
    # on Windows without the `tzdata` package (zoneinfo has no bundled IANA database there).
    _cols = ["location_id", "type_id", "is_buy_order", "price"]
    join_keys = ["location_id", "type_id"]
    old_scoped = old_df.select(_cols).join(relevant_keys, on=join_keys, how="inner")
    new_scoped = new_df.select(_cols).join(relevant_keys, on=join_keys, how="inner")

    old_best_bid, old_best_ask = _best_prices(old_scoped)
    new_best_bid, new_best_ask = _best_prices(new_scoped)
    bid_competitors, ask_competitors = _competitor_counts(
        new_scoped, new_best_bid, new_best_ask, near_top_pct
    )

    bid_volume: dict[tuple[int, int], int] = defaultdict(int)
    ask_volume: dict[tuple[int, int], int] = defaultdict(int)
    bid_relists: dict[tuple[int, int], int] = defaultdict(int)
    ask_relists: dict[tuple[int, int], int] = defaultdict(int)
    region_by_key: dict[tuple[int, int], int] = {}

    for e in events:
        key = (e["location_id"], e["type_id"])
        region_by_key[key] = e["region_id"]
        if e["event_type"] == RELIST:
            (bid_relists if e["is_buy_order"] else ask_relists)[key] += 1
        elif e["event_type"] == FILL:
            best = old_best_bid.get(key) if e["is_buy_order"] else old_best_ask.get(key)
            if best is None:
                continue
            near_top = (
                e["price"] >= best * (1 - near_top_pct)
                if e["is_buy_order"]
                else e["price"] <= best * (1 + near_top_pct)
            )
            if near_top:
                (bid_volume if e["is_buy_order"] else ask_volume)[key] += e["filled_volume"]

    all_keys = (
        set(bid_volume)
        | set(ask_volume)
        | set(bid_relists)
        | set(ask_relists)
        | set(bid_competitors)
        | set(ask_competitors)
    )

    rows = []
    for location_id, type_id in all_keys:
        key = (location_id, type_id)
        bid_count = bid_competitors.get(key, 0)
        ask_count = ask_competitors.get(key, 0)
        rows.append(
            {
                "region_id": region_by_key[key],
                "location_id": location_id,
                "type_id": type_id,
                "from_snapshot_id": from_snapshot_id,
                "to_snapshot_id": to_snapshot_id,
                "from_fetched_at": from_fetched_at,
                "to_fetched_at": to_fetched_at,
                "elapsed_hours": elapsed_hours,
                "bid_volume_per_hour": bid_volume.get(key, 0) / elapsed_hours,
                "ask_volume_per_hour": ask_volume.get(key, 0) / elapsed_hours,
                "relists_per_hour": (bid_relists.get(key, 0) + ask_relists.get(key, 0))
                / elapsed_hours,
                "bid_competitor_count": bid_count,
                "ask_competitor_count": ask_count,
                "estimated_bid_capture_rate": estimate_capture_rate(bid_count),
                "estimated_ask_capture_rate": estimate_capture_rate(ask_count),
            }
        )
    return rows


@dataclass
class DiffRunResult:
    region_id: int
    pairs_processed: int
    pairs_skipped_missing_file: int
    event_counts: dict[str, int]


def _snapshot_parquet_path(raw_data_dir: str, region_id: int, snapshot: SnapshotMeta) -> Path:
    return (
        Path(raw_data_dir)
        / f"region={region_id}"
        / f"date={snapshot.fetched_at:%Y-%m-%d}"
        / f"{snapshot.snapshot_id}.parquet"
    )


def run_diff_for_region(
    store: DuckDBStore,
    region_id: int,
    settings: Settings,
    *,
    skip_existing: bool = False,
    since: datetime | None = None,
) -> DiffRunResult:
    """Diff consecutive pairs of retained raw snapshots for `region_id` (idempotent).

    Default: every retained pair. `skip_existing` leaves pairs that already have metrics
    alone and `since` ignores pairs ending before it -- together they make a cheap
    incremental run for cron (only the snapshots that arrived since the last one).
    """
    history = store.snapshot_history(region_id)
    already = store.diffed_pairs(region_id) if skip_existing else set()
    event_counts: dict[str, int] = defaultdict(int)
    pairs_processed = 0
    pairs_skipped = 0
    all_events: list[dict] = []
    all_metrics: list[dict] = []

    for old_snap, new_snap in zip(history, history[1:], strict=False):
        if (old_snap.snapshot_id, new_snap.snapshot_id) in already:
            continue
        if since is not None and new_snap.fetched_at < since:
            continue
        old_path = _snapshot_parquet_path(settings.storage.raw_data_dir, region_id, old_snap)
        new_path = _snapshot_parquet_path(settings.storage.raw_data_dir, region_id, new_snap)
        if not old_path.exists() or not new_path.exists():
            logger.warning(
                "skipping diff %s -> %s: raw snapshot file missing (pruned?)",
                old_snap.snapshot_id,
                new_snap.snapshot_id,
            )
            pairs_skipped += 1
            continue

        old_df = pl.read_parquet(old_path)
        new_df = pl.read_parquet(new_path)
        history_volume = store.history_volume_map(region_id, new_snap.fetched_at.date())

        events = diff_snapshots(
            old_df,
            new_df,
            old_snap.snapshot_id,
            new_snap.snapshot_id,
            old_snap.fetched_at,
            new_snap.fetched_at,
            history_volume,
            settings.fill,
        )
        metrics = compute_book_metrics(
            events,
            old_df,
            new_df,
            old_snap.snapshot_id,
            new_snap.snapshot_id,
            old_snap.fetched_at,
            new_snap.fetched_at,
            settings.features.near_top_pct,
        )
        all_events.extend(events)
        all_metrics.extend(metrics)
        for e in events:
            event_counts[e["event_type"]] += 1
        pairs_processed += 1

    if all_metrics:
        store.write_diff_results(all_events, all_metrics)

    logger.info(
        "diffed region %d: %d pairs processed, %d skipped (missing files), events=%s",
        region_id,
        pairs_processed,
        pairs_skipped,
        dict(event_counts),
    )
    return DiffRunResult(
        region_id=region_id,
        pairs_processed=pairs_processed,
        pairs_skipped_missing_file=pairs_skipped,
        event_counts=dict(event_counts),
    )


@dataclass
class CalibrationResult:
    region_id: int
    type_id: int
    target_date: date
    estimated_volume: float
    actual_volume: float | None
    ratio: float | None


def calibration_check(
    store: DuckDBStore, region_id: int, type_id: int, target_date: date
) -> CalibrationResult:
    """Compare summed estimated fills for `target_date` against the real history volume."""
    estimated = store.calibration_estimated_volume(region_id, type_id, target_date)
    actual = store.calibration_actual_volume(region_id, type_id, target_date)
    ratio = (estimated / actual) if actual else None
    return CalibrationResult(
        region_id=region_id,
        type_id=type_id,
        target_date=target_date,
        estimated_volume=estimated,
        actual_volume=actual,
        ratio=ratio,
    )
