"""Tests for resolving station/region names via ESI. No network -- fake client + real fixtures."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from eve.ingest.locations import resolve_locations
from eve.store.duckdb_store import DuckDBStore

FIXTURES = Path(__file__).parent / "fixtures" / "esi"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeEsiClient:
    """Stands in for EsiClient.get_json -- never touches the network."""

    max_concurrency = 5

    def __init__(self, responses: dict[str, dict], missing: frozenset[str] = frozenset()) -> None:
        self._responses = responses
        self._missing = missing
        self.calls: list[str] = []

    async def get_json(self, path: str, *, params: dict | None = None) -> dict:
        self.calls.append(path)
        if path in self._missing:
            request = httpx.Request("GET", "https://esi.evetech.net" + path)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return self._responses[path]


def _seed(store: DuckDBStore, *, region_id: int, station_ids: list[int]) -> None:
    now = datetime.now(UTC)
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        [
            "snap-1",
            region_id,
            now.replace(tzinfo=None),
            (now + timedelta(minutes=5)).replace(tzinfo=None),
            len(station_ids),
        ],
    )
    for order_id, location_id in enumerate(station_ids, start=1):
        store._con.execute(
            "INSERT INTO orders_latest (order_id, type_id, location_id, region_id) "
            "VALUES (?, ?, ?, ?)",
            [order_id, 34, location_id, region_id],
        )


def test_resolve_locations_writes_station_and_region_names(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed(store, region_id=10000002, station_ids=[60003760])
        client = FakeEsiClient(
            {
                "/universe/regions/10000002/": _load("region_10000002.json"),
                "/universe/stations/60003760/": _load("station_60003760.json"),
            }
        )

        result = asyncio.run(resolve_locations(store, client))

        assert result.resolved == 2
        assert result.unresolved == 0

        row = store._con.execute(
            "SELECT kind, name, system_id FROM locations WHERE location_id = 60003760"
        ).fetchone()
        assert row == ("station", "Jita IV - Moon 4 - Caldari Navy Assembly Plant", 30000142)

        row = store._con.execute(
            "SELECT kind, name FROM locations WHERE location_id = 10000002"
        ).fetchone()
        assert row == ("region", "The Forge")


def test_resolve_locations_skips_structures_without_calling_esi(tmp_path: Path) -> None:
    structure_id = 1035466617946  # player citizen structure -- auth ESI, out of v1 scope

    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed(store, region_id=10000002, station_ids=[60003760, structure_id])
        client = FakeEsiClient(
            {
                "/universe/regions/10000002/": _load("region_10000002.json"),
                "/universe/stations/60003760/": _load("station_60003760.json"),
            }
        )

        result = asyncio.run(resolve_locations(store, client))

        assert result.resolved == 2
        assert f"/universe/stations/{structure_id}/" not in client.calls
        count = store._con.execute(
            "SELECT COUNT(*) FROM locations WHERE location_id = ?", [structure_id]
        ).fetchone()[0]
        assert count == 0


def test_resolve_locations_logs_unresolvable_station_without_crashing(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed(store, region_id=10000002, station_ids=[60003760, 60000001])
        client = FakeEsiClient(
            {
                "/universe/regions/10000002/": _load("region_10000002.json"),
                "/universe/stations/60003760/": _load("station_60003760.json"),
            },
            missing=frozenset({"/universe/stations/60000001/"}),
        )

        result = asyncio.run(resolve_locations(store, client))

        assert result.resolved == 2  # region + the one resolvable station
        assert result.unresolved == 1
        count = store._con.execute(
            "SELECT COUNT(*) FROM locations WHERE location_id = 60000001"
        ).fetchone()[0]
        assert count == 0
