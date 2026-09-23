"""Tests for the cron-safe ingest wrapper: outcome recording, failure handling, and
region resolution from `station_pairs`. Reuses the `FakeEsiClient` pattern from
test_ingest_orders.py -- never touches the network."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Filters,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.ingest.esi_client import RegionOrdersResult
from eve.ops.ingest_runner import resolve_regions, run_and_record_history, run_and_record_ingest
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 10000002
SYS_A, SYS_B = 30000142, 30002187
STATION_A, STATION_B = 60003760, 60008494


class FakeEsiClient:
    def __init__(
        self,
        orders: list[dict] | None = None,
        expires_at: datetime | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.orders = orders or []
        self.expires_at = expires_at or (datetime.now(UTC) + timedelta(minutes=5))
        self.fail = fail
        self.call_count = 0

    async def fetch_region_orders(self, region_id: int) -> RegionOrdersResult:
        self.call_count += 1
        if self.fail:
            raise RuntimeError("simulated ESI failure")
        return RegionOrdersResult(orders=self.orders, expires_at=self.expires_at)

    async def fetch_history(self, region_id: int, type_id: int) -> list[dict]:
        self.call_count += 1
        if self.fail:
            raise RuntimeError("simulated ESI failure")
        return []


def _settings(tmp_path: Path, station_pairs: list[StationPair] | None = None) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=station_pairs
        or [StationPair(from_station_id=STATION_A, to_station_id=STATION_B)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(
            raw_data_dir=str(tmp_path / "raw"), duckdb_path=str(tmp_path / "eve.duckdb")
        ),
    )


def _order(order_id: int) -> dict:
    return {
        "order_id": order_id,
        "type_id": 34,
        "location_id": STATION_A,
        "system_id": SYS_A,
        "is_buy_order": False,
        "price": 5.0,
        "volume_total": 100,
        "volume_remain": 100,
        "min_volume": 1,
        "duration": 90,
        "issued": "2026-09-20T00:00:00Z",
        "range": "station",
    }


def test_run_and_record_ingest_success_writes_ingest_runs_row(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = FakeEsiClient([_order(1), _order(2)])

    with DuckDBStore(settings.storage.duckdb_path) as store:
        outcome = asyncio.run(run_and_record_ingest(store, REGION_ID, settings, client=client))
        rows = store._con.execute(
            "SELECT command, region_id, success, order_count, error_message FROM ingest_runs"
        ).fetchall()

    assert outcome.success is True
    assert outcome.order_count == 2
    assert rows == [("ingest", REGION_ID, True, 2, None)]


def test_run_and_record_ingest_failure_is_caught_and_recorded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = FakeEsiClient(fail=True)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        outcome = asyncio.run(run_and_record_ingest(store, REGION_ID, settings, client=client))
        rows = store._con.execute(
            "SELECT command, region_id, success, order_count, error_message FROM ingest_runs"
        ).fetchall()

    assert outcome.success is False
    assert "simulated ESI failure" in outcome.error_message
    assert len(rows) == 1
    assert rows[0][2] is False
    assert "simulated ESI failure" in rows[0][4]


def test_run_and_record_history_reports_per_type_failures(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = FakeEsiClient(fail=True)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        store._con.execute(
            """
            INSERT INTO orders_latest
            (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
             volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
            VALUES (1, 34, ?, ?, false, 5.0, 100, 100, 1, 90, 'station', ?, 'snap-1', ?)
            """,
            [STATION_A, SYS_A, REGION_ID, datetime(2026, 9, 20, tzinfo=UTC).replace(tzinfo=None)],
        )

        outcome = asyncio.run(run_and_record_history(store, REGION_ID, settings, client=client))
        rows = store._con.execute(
            "SELECT command, region_id, success, error_message FROM ingest_runs"
        ).fetchall()

    # ingest_history's own per-type try/except swallows the fetch_history exception into
    # HistoryResult.failed -- the wrapper turns a non-zero failed count into success=False.
    assert outcome.success is False
    assert "1 type(s) failed" in outcome.error_message
    assert rows == [("ingest-history", REGION_ID, False, "1 type(s) failed")]


def test_resolve_regions_from_station_pairs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        store._con.execute(
            "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_A, 10000002, 1, 0.9, "Jita"]
        )
        store._con.execute(
            "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)", [SYS_B, 10000043, 1, 0.9, "Amarr"]
        )
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_A, "Jita", SYS_A]
        )
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)", [STATION_B, "Amarr", SYS_B]
        )

        regions = resolve_regions(store, settings)

    assert regions == {10000002, 10000043}


def test_resolve_regions_empty_when_locations_not_loaded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        regions = resolve_regions(store, settings)

    assert regions == set()
