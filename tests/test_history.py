"""Tests for daily market history ingest: resumable, once per (region, type) per day."""

from __future__ import annotations

import asyncio
import json
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
from eve.ingest.history import ingest_history
from eve.store.duckdb_store import DuckDBStore

FIXTURES = Path(__file__).parent / "fixtures" / "esi"


def _load_history() -> list[dict]:
    path = FIXTURES / "history_type34_region10000002.json"
    return json.loads(path.read_text(encoding="utf-8"))


class FakeEsiClient:
    """Stands in for EsiClient.fetch_history -- never touches the network."""

    def __init__(self, history_by_type: dict[int, list[dict]]) -> None:
        self._history_by_type = history_by_type
        self.calls: list[tuple[int, int]] = []

    async def fetch_history(self, region_id: int, type_id: int) -> list[dict]:
        self.calls.append((region_id, type_id))
        return self._history_by_type.get(type_id, [])


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=1, to_station_id=2)],
        filters=Filters(),
        esi=Esi(contact="test@example.com", max_concurrency=5),
        storage=Storage(duckdb_path=str(tmp_path / "eve.duckdb")),
    )


def _seed_orders(store: DuckDBStore, region_id: int, type_ids: list[int]) -> None:
    for order_id, type_id in enumerate(type_ids, start=1):
        store._con.execute(
            "INSERT INTO orders_latest (order_id, type_id, region_id) VALUES (?, ?, ?)",
            [order_id, type_id, region_id],
        )


def test_ingest_history_fetches_and_stores(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    history_rows = _load_history()

    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_orders(store, 10000002, [34, 587])
        fake_client = FakeEsiClient({34: history_rows, 587: []})

        result = asyncio.run(ingest_history(10000002, settings, store=store, client=fake_client))

        assert result.fetched == 2
        assert result.failed == 0
        assert result.skipped_already_done == 0
        assert len(fake_client.calls) == 2

        count = store._con.execute("SELECT COUNT(*) FROM history WHERE type_id = 34").fetchone()[0]
        assert count == len(history_rows)

        # type 587 had zero history rows, but must still be marked "done today" so it
        # isn't re-fetched forever.
        log_count = store._con.execute(
            "SELECT COUNT(*) FROM history_fetch_log WHERE type_id = 587"
        ).fetchone()[0]
        assert log_count == 1


def test_ingest_history_resumes_without_refetching_done_types(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    history_rows = _load_history()

    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_orders(store, 10000002, [34, 587])
        fake_client = FakeEsiClient({34: history_rows, 587: []})

        first = asyncio.run(ingest_history(10000002, settings, store=store, client=fake_client))
        second = asyncio.run(ingest_history(10000002, settings, store=store, client=fake_client))

        assert first.fetched == 2
        assert second.fetched == 0
        assert second.skipped_already_done == 2
        assert len(fake_client.calls) == 2  # the second run made zero new calls
