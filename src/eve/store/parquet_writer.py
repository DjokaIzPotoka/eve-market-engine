"""Raw snapshot storage as partitioned Parquet, with retention pruning."""

from __future__ import annotations

import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


def write_raw_snapshot(
    orders_df: pl.DataFrame,
    region_id: int,
    snapshot_id: str,
    fetched_at: datetime,
    raw_data_dir: str | Path,
) -> Path:
    """Write the snapshot as `<raw_data_dir>/region=<id>/date=<YYYY-MM-DD>/<id>.parquet`."""
    partition_dir = Path(raw_data_dir) / f"region={region_id}" / f"date={fetched_at:%Y-%m-%d}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    out_path = partition_dir / f"{snapshot_id}.parquet"
    orders_df.write_parquet(out_path)
    return out_path


def prune_raw_snapshots(
    raw_data_dir: str | Path, retention_days: int, now: date | None = None
) -> list[Path]:
    """Delete `date=` partitions older than `retention_days`. Returns the removed paths."""
    root = Path(raw_data_dir)
    if not root.exists():
        return []

    cutoff = (now or date.today()) - timedelta(days=retention_days)
    removed: list[Path] = []
    for region_dir in root.glob("region=*"):
        for date_dir in region_dir.glob("date=*"):
            raw_date = date_dir.name.removeprefix("date=")
            try:
                partition_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            if partition_date < cutoff:
                shutil.rmtree(date_dir)
                removed.append(date_dir)
                logger.info(
                    "pruned raw snapshot partition %s (older than %d days)",
                    date_dir,
                    retention_days,
                )
    return removed
