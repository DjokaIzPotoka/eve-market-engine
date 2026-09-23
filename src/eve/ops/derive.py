"""Derived data refresh: book features + snapshot diffs, run after each ingest.

`eve ingest` only stores raw orders; market making reads `book_features` (best bid/ask per
station) and `book_diff_metrics` (fill/relist rates). Left manual they go stale within hours
-- the dashboard would rank yesterday's books. This does the incremental version cheaply
enough for cron: features only for the configured pair stations and only when the latest
snapshot has none yet, diffs only for snapshot pairs not yet processed and recent enough to
matter for the trailing metrics window.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from eve.config import Settings
from eve.features.book_features import compute_book_features
from eve.fill.fill_model import run_diff_for_region
from eve.ops.ingest_runner import resolve_regions
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)

COMMAND = "derive"


@dataclass(frozen=True)
class DeriveOutcome:
    region_id: int
    success: bool
    feature_rows: int
    pairs_diffed: int
    error_message: str | None = None


def _pair_stations_by_region(store: DuckDBStore, settings: Settings) -> dict[int, list[int]]:
    station_system = dict(store.station_locations())
    system_region = store.system_region_map()
    by_region: dict[int, set[int]] = {}
    for pair in settings.station_pairs:
        for station_id in (pair.from_station_id, pair.to_station_id):
            region_id = system_region.get(station_system.get(station_id, -1))
            if region_id is not None:
                by_region.setdefault(region_id, set()).add(station_id)
    return {region: sorted(ids) for region, ids in by_region.items()}


def derive_region(
    store: DuckDBStore, region_id: int, station_ids: list[int], settings: Settings
) -> DeriveOutcome:
    now = datetime.now(UTC)
    started_at = now
    run_id = str(uuid.uuid4())
    try:
        feature_rows = 0
        snapshot = store.latest_snapshot_meta(region_id)
        if snapshot is not None and not store.has_book_features(snapshot.snapshot_id):
            feature_rows = compute_book_features(
                store, region_id, settings, location_ids=station_ids
            )

        since = now - timedelta(hours=settings.fill.metrics_window_hours + 1)
        diff = run_diff_for_region(store, region_id, settings, skip_existing=True, since=since)

        store.prune_book_features(now - timedelta(days=settings.features.retention_days))
    except Exception as exc:
        logger.exception("derive failed for region %d", region_id)
        store.write_ingest_run(
            run_id, COMMAND, region_id, started_at, datetime.now(UTC), False, error_message=str(exc)
        )
        return DeriveOutcome(region_id, False, 0, 0, str(exc))

    store.write_ingest_run(
        run_id, COMMAND, region_id, started_at, datetime.now(UTC), True, order_count=feature_rows
    )
    return DeriveOutcome(region_id, True, feature_rows, diff.pairs_processed)


def run_derive_all(store: DuckDBStore, settings: Settings) -> list[DeriveOutcome]:
    by_region = _pair_stations_by_region(store, settings)
    return [
        derive_region(store, region_id, by_region.get(region_id, []), settings)
        for region_id in sorted(resolve_regions(store, settings))
    ]
