"""Cron-safe wrappers around `ingest_region`/`ingest_history`: catch exceptions and record
every attempt (success or failure) to `ingest_runs`, so `eve health` has something to
report on. Neither ingest function is touched -- both already accept an optional `store`,
so this just supplies one and writes the outcome afterward.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from eve.config import Settings
from eve.ingest.esi_client import EsiClient
from eve.ingest.history import ingest_history
from eve.ingest.orders import ingest_region
from eve.ops.telemetry import record_telemetry
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestRunOutcome:
    run_id: str
    region_id: int
    success: bool
    order_count: int | None
    error_message: str | None
    skipped: bool = False


def resolve_regions(store: DuckDBStore, settings: Settings) -> set[int]:
    """Every region touched by `settings.station_pairs`, via the station->system->region
    chain -- requires `locations`/`solar_systems` to already be loaded (`eve load-sde`)."""
    station_system = dict(store.station_locations())
    system_region = store.system_region_map()
    station_ids = {
        sid for pair in settings.station_pairs for sid in (pair.from_station_id, pair.to_station_id)
    }
    regions: set[int] = set()
    for station_id in station_ids:
        system_id = station_system.get(station_id)
        region_id = system_region.get(system_id) if system_id is not None else None
        if region_id is not None:
            regions.add(region_id)
    if not regions:
        logger.warning("no regions resolved from station_pairs -- has `eve load-sde` run?")
    return regions


async def run_and_record_ingest(
    store: DuckDBStore, region_id: int, settings: Settings, client: EsiClient | None = None
) -> IngestRunOutcome:
    # Own the client here (rather than inside ingest) so its last ESI error-limit reading is
    # still available to telemetry after the run, success or failure.
    owns_client = client is None
    client = client or EsiClient(settings.esi)
    try:
        return await _run_and_record_ingest(store, region_id, settings, client)
    finally:
        record_telemetry(store, settings, client)
        if owns_client:
            await client.aclose()


async def _run_and_record_ingest(
    store: DuckDBStore, region_id: int, settings: Settings, client: EsiClient | None
) -> IngestRunOutcome:
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    try:
        result = await ingest_region(region_id, settings, store=store, client=client)
    except Exception as exc:
        logger.exception("ingest failed for region %d", region_id)
        store.write_ingest_run(
            run_id,
            "ingest",
            region_id,
            started_at,
            datetime.now(UTC),
            False,
            error_message=str(exc),
        )
        return IngestRunOutcome(run_id, region_id, False, None, str(exc))

    store.write_ingest_run(
        run_id,
        "ingest",
        region_id,
        started_at,
        datetime.now(UTC),
        True,
        order_count=result.order_count,
    )
    return IngestRunOutcome(
        run_id, region_id, True, result.order_count, None, skipped=result.skipped
    )


async def run_and_record_history(
    store: DuckDBStore, region_id: int, settings: Settings, client: EsiClient | None = None
) -> IngestRunOutcome:
    # Own the client here (rather than inside ingest) so its last ESI error-limit reading is
    # still available to telemetry after the run, success or failure.
    owns_client = client is None
    client = client or EsiClient(settings.esi)
    try:
        return await _run_and_record_history(store, region_id, settings, client)
    finally:
        record_telemetry(store, settings, client)
        if owns_client:
            await client.aclose()


async def _run_and_record_history(
    store: DuckDBStore, region_id: int, settings: Settings, client: EsiClient | None
) -> IngestRunOutcome:
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    try:
        result = await ingest_history(region_id, settings, store=store, client=client)
    except Exception as exc:
        logger.exception("history ingest failed for region %d", region_id)
        store.write_ingest_run(
            run_id,
            "ingest-history",
            region_id,
            started_at,
            datetime.now(UTC),
            False,
            error_message=str(exc),
        )
        return IngestRunOutcome(run_id, region_id, False, None, str(exc))

    # `ingest_history` already catches per-type failures internally -- surface a non-zero
    # count as a (non-fatal) error message rather than a raised exception.
    success = result.failed == 0
    error_message = f"{result.failed} type(s) failed" if result.failed else None
    store.write_ingest_run(
        run_id,
        "ingest-history",
        region_id,
        started_at,
        datetime.now(UTC),
        success,
        order_count=result.fetched,
        error_message=error_message,
    )
    return IngestRunOutcome(run_id, region_id, success, result.fetched, error_message)


async def run_ingest_all(store: DuckDBStore, settings: Settings) -> list[IngestRunOutcome]:
    return [
        await run_and_record_ingest(store, region_id, settings)
        for region_id in sorted(resolve_regions(store, settings))
    ]


async def run_ingest_history_all(store: DuckDBStore, settings: Settings) -> list[IngestRunOutcome]:
    return [
        await run_and_record_history(store, region_id, settings)
        for region_id in sorted(resolve_regions(store, settings))
    ]
