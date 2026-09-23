"""Health check (Faza 8): last snapshot / staleness / order count per region, plus recent
ingest errors. Regions come from `resolve_regions` (derived from `station_pairs`), not a
separate config list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from eve.config import Settings
from eve.ops.ingest_runner import resolve_regions
from eve.store.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class RegionHealth:
    region_id: int
    last_snapshot_id: str | None
    last_fetched_at: datetime | None
    order_count: int | None
    age_minutes: float | None
    stale: bool


@dataclass(frozen=True)
class IngestError:
    command: str
    region_id: int
    error_message: str


@dataclass(frozen=True)
class HealthReport:
    regions: list[RegionHealth]
    recent_errors: list[IngestError]

    @property
    def healthy(self) -> bool:
        return not self.recent_errors and all(not r.stale for r in self.regions)


def check_health(store: DuckDBStore, settings: Settings) -> HealthReport:
    now = datetime.now(UTC)
    regions = []
    for region_id in sorted(resolve_regions(store, settings)):
        snapshot = store.latest_snapshot_meta(region_id)
        if snapshot is None:
            regions.append(RegionHealth(region_id, None, None, None, None, stale=True))
            continue
        age_minutes = (now - snapshot.fetched_at).total_seconds() / 60
        regions.append(
            RegionHealth(
                region_id=region_id,
                last_snapshot_id=snapshot.snapshot_id,
                last_fetched_at=snapshot.fetched_at,
                order_count=snapshot.order_count,
                age_minutes=age_minutes,
                stale=age_minutes > settings.health.stale_after_minutes,
            )
        )

    since = now - timedelta(hours=settings.health.error_lookback_hours)
    recent_errors = [
        IngestError(command=row[1], region_id=row[2], error_message=row[7])
        for row in store.recent_ingest_runs(since)
        if not row[5]  # success column
    ]

    return HealthReport(regions=regions, recent_errors=recent_errors)
