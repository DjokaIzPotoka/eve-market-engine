"""Telemetry sampling: ESI error limit + disk usage, recorded at the end of ingest runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_ops_ingest_runner import REGION_ID, FakeEsiClient, _order, _settings

from eve.ops.ingest_runner import run_and_record_ingest
from eve.ops.telemetry import DISK_SAMPLE_INTERVAL, RETENTION, record_telemetry
from eve.store.duckdb_store import DuckDBStore

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class _Client:
    last_error_limit = (97, 42.0)


def test_records_esi_limit_and_disk_sample(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        record_telemetry(store, settings, _Client(), now=NOW)

        (_, remain, reset) = store.latest_esi_limit_sample()
        assert (remain, reset) == (97, 42.0)
        (sample,) = store.disk_samples(NOW - timedelta(days=1))
        assert 0 < sample[1] <= sample[2]


def test_no_network_run_writes_no_esi_sample(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        record_telemetry(store, settings, FakeEsiClient(), now=NOW)  # no last_error_limit attr

        assert store.latest_esi_limit_sample() is None
        assert len(store.disk_samples(NOW - timedelta(days=1))) == 1


def test_disk_sampling_is_throttled(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        record_telemetry(store, settings, None, now=NOW)
        record_telemetry(store, settings, None, now=NOW + timedelta(minutes=5))
        record_telemetry(store, settings, None, now=NOW + DISK_SAMPLE_INTERVAL)

        assert len(store.disk_samples(NOW - timedelta(days=1))) == 2


def test_old_samples_are_pruned(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.write_disk_sample(NOW - RETENTION - timedelta(days=1), 1, 2)
        store.write_esi_limit_sample(NOW - RETENTION - timedelta(days=1), 50, 1.0)

        record_telemetry(store, settings, None, now=NOW)

        assert [s[0].replace(tzinfo=UTC) for s in store.disk_samples(NOW - timedelta(days=90))] == [
            NOW.replace(tzinfo=UTC)
        ]
        assert store.latest_esi_limit_sample() is None


def test_telemetry_failure_never_fails_the_ingest(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        DuckDBStore, "write_disk_sample", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with DuckDBStore(settings.storage.duckdb_path) as store:
        store._con.execute(
            "INSERT INTO solar_systems VALUES (30000142, ?, 1, 0.9, 'Jita')", [REGION_ID]
        )
        outcome = asyncio.run(
            run_and_record_ingest(store, REGION_ID, settings, client=FakeEsiClient([_order(1)]))
        )

    assert outcome.success is True
