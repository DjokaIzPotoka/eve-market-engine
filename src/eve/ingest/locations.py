"""Resolve station and region names via ESI (the official SDE JSONL export has neither)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from eve.ingest.esi_client import EsiClient
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


@dataclass
class LocationsResult:
    resolved: int
    unresolved: int


async def resolve_locations(store: DuckDBStore, client: EsiClient) -> LocationsResult:
    """Fetch and store names for regions (`snapshots`) and NPC stations (`orders_latest`)."""
    resolved = 0
    unresolved = 0

    for region_id in store.pending_region_ids():
        try:
            data = await client.get_json(f"/universe/regions/{region_id}/")
        except httpx.HTTPStatusError as exc:
            logger.warning("could not resolve region %d: %s", region_id, exc)
            unresolved += 1
            continue
        store.write_location(region_id, kind="region", name=data["name"])
        resolved += 1

    station_ids = store.pending_station_ids()
    semaphore = asyncio.Semaphore(client.max_concurrency)

    async def resolve_station(station_id: int) -> None:
        nonlocal resolved, unresolved
        async with semaphore:
            try:
                data = await client.get_json(f"/universe/stations/{station_id}/")
            except httpx.HTTPStatusError as exc:
                logger.warning("could not resolve station %d: %s", station_id, exc)
                unresolved += 1
                return
        store.write_location(
            station_id, kind="station", name=data["name"], system_id=data.get("system_id")
        )
        resolved += 1

    await asyncio.gather(*(resolve_station(sid) for sid in station_ids))

    return LocationsResult(resolved=resolved, unresolved=unresolved)
