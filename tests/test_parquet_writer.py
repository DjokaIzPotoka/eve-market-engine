"""Tests for raw Parquet snapshot writer and retention pruning."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from eve.store.parquet_writer import prune_raw_snapshots, write_raw_snapshot


def _df() -> pl.DataFrame:
    return pl.DataFrame({"order_id": [1, 2], "price": [1.0, 2.0]})


def test_write_raw_snapshot_partitions_by_region_and_date(tmp_path: Path) -> None:
    fetched_at = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    out_path = write_raw_snapshot(_df(), 10000002, "snap-1", fetched_at, tmp_path)

    expected = tmp_path / "region=10000002" / "date=2026-09-20" / "snap-1.parquet"
    assert out_path == expected
    assert out_path.exists()
    assert pl.read_parquet(out_path).height == 2


def test_prune_raw_snapshots_removes_old_partitions_only(tmp_path: Path) -> None:
    old_dir = tmp_path / "region=10000002" / "date=2026-01-01"
    new_dir = tmp_path / "region=10000002" / "date=2026-09-20"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    (old_dir / "snap.parquet").write_bytes(b"x")
    (new_dir / "snap.parquet").write_bytes(b"x")

    removed = prune_raw_snapshots(tmp_path, retention_days=30, now=date(2026, 9, 21))

    assert removed == [old_dir]
    assert not old_dir.exists()
    assert new_dir.exists()


def test_prune_raw_snapshots_on_missing_dir_is_noop(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert prune_raw_snapshots(missing, retention_days=30) == []
