"""Ops telemetry for the dashboard Status screen: the ESI error limit as last reported and
volume disk usage, sampled at the end of every ingest run (cron already runs those every
5-15 minutes, so no new scheduled job is needed).

Best-effort by design: a telemetry failure must never fail or mask the ingest it rides on.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eve.config import Settings
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)

# Disk fills over days, so a sample every half hour is plenty for a 14-day chart.
DISK_SAMPLE_INTERVAL = timedelta(minutes=30)
RETENTION = timedelta(days=30)


def record_telemetry(
    store: DuckDBStore, settings: Settings, client: object | None, now: datetime | None = None
) -> None:
    now = now or datetime.now(UTC)
    try:
        # A run that never touched the network (Expires not reached) has nothing new to report.
        limit = getattr(client, "last_error_limit", None)
        if limit is not None:
            store.write_esi_limit_sample(now, limit[0], limit[1])

        last_disk = store.latest_disk_sample_time()
        if last_disk is None or now - last_disk >= DISK_SAMPLE_INTERVAL:
            usage = shutil.disk_usage(Path(settings.storage.duckdb_path).resolve().parent)
            store.write_disk_sample(now, usage.used, usage.total)

        store.prune_telemetry(now - RETENTION)
    except Exception:
        logger.exception("telemetry sampling failed (ignored)")
