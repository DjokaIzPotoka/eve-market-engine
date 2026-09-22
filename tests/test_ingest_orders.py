"""Tests for ingest_region orchestration: fetch/store, and Expires-based caching."""

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
from eve.ingest.orders import ingest_region
from eve.store.duckdb_store import DuckDBStore


class FakeEsiClient:
    """Stands in for EsiClient in orchestration tests -- never touches the network."""

    def __init__(self, orders: list[dict], expires_at: datetime) -> None:
        self.orders = orders
        self.expires_at = expires_at
        self.call_count = 0

    async def fetch_region_orders(self, region_id: int) -> RegionOrdersResult:
        self.call_count += 1
        return RegionOrdersResult(orders=self.orders, expires_at=self.expires_at)


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=1, to_station_id=2)],
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
        "location_id": 60003760,
        "system_id": 30000142,
        "is_buy_order": False,
        "price": 5.0,
        "volume_total": 100,
        "volume_remain": 100,
        "min_volume": 1,
        "duration": 90,
        "issued": "2026-09-20T00:00:00Z",
        "range": "station",
    }


def test_ingest_region_fetches_and_stores(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    fake_client = FakeEsiClient([_order(1), _order(2)], expires_at)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        result = asyncio.run(ingest_region(10000002, settings, store=store, client=fake_client))

    assert result.skipped is False
    assert result.order_count == 2
    assert fake_client.call_count == 1

    parquet_files = list((tmp_path / "raw").rglob("*.parquet"))
    assert len(parquet_files) == 1


def test_ingest_region_skips_fetch_when_snapshot_still_fresh(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    fake_client = FakeEsiClient([_order(1)], expires_at)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        first = asyncio.run(ingest_region(10000002, settings, store=store, client=fake_client))
        second = asyncio.run(ingest_region(10000002, settings, store=store, client=fake_client))

    assert first.skipped is False
    assert second.skipped is True
    assert second.snapshot_id == first.snapshot_id
    assert fake_client.call_count == 1
