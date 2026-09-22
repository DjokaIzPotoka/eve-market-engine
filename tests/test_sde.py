"""Tests for SDE loading: zip extraction, type load, and build-based skip. No network."""

from __future__ import annotations

import asyncio
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
from eve.ingest.sde import check_and_load_sde, extract_member
from eve.store.duckdb_store import DuckDBStore

FIXTURES = Path(__file__).parent / "fixtures" / "sde"


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
            duckdb_path=str(tmp_path / "eve.duckdb"),
            sde_dir=str(tmp_path / "sde"),
        ),
    )


def test_extract_member_picks_correct_file_not_the_decoy(tmp_path: Path) -> None:
    types_path = extract_member(FIXTURES / "sample-jsonl.zip", "types.jsonl", tmp_path)

    assert types_path.name == "types.jsonl"
    content = types_path.read_text(encoding="utf-8")
    assert '"_key": 34' in content  # Tritanium
    assert "decoy" not in content  # archetypes.jsonl in the fixture must not be picked


def test_load_types_from_jsonl_sets_packaged_volume_for_ships(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        count = store.load_types_from_jsonl(FIXTURES / "types_sample.jsonl", build_number=123)

        assert count > 0
        assert store.latest_sde_build() == 123

        row = store._con.execute(
            "SELECT name, packaged_volume FROM types WHERE type_id = 587"
        ).fetchone()
        assert row == ("Rifter", 2500.0)  # packaged volume, not the full hull volume


def test_check_and_load_sde_loads_from_injected_build_and_zip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        result = asyncio.run(
            check_and_load_sde(
                store, settings, build_number=123, zip_path=FIXTURES / "sample-jsonl.zip"
            )
        )

        assert result.skipped is False
        assert result.build_number == 123
        assert result.types_loaded > 0
        assert store.latest_sde_build() == 123
        assert store.has_system_topology()

        jita_row = store._con.execute(
            "SELECT region_id, name FROM solar_systems WHERE system_id = 30000142"
        ).fetchone()
        assert jita_row == (10000002, "Jita")
        assert len(store.jump_graph()) > 0


def test_check_and_load_sde_skips_when_build_unchanged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        first = asyncio.run(
            check_and_load_sde(
                store, settings, build_number=123, zip_path=FIXTURES / "sample-jsonl.zip"
            )
        )
        second = asyncio.run(check_and_load_sde(store, settings, build_number=123))

        assert first.skipped is False
        assert second.skipped is True
        assert second.types_loaded == 0


def test_check_and_load_sde_does_not_skip_when_topology_missing(tmp_path: Path) -> None:
    """A store from before topology-loading existed (build set, no solar_systems) must not skip."""
    settings = _settings(tmp_path)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        store.load_types_from_jsonl(FIXTURES / "types_sample.jsonl", build_number=123)
        assert store.latest_sde_build() == 123
        assert not store.has_system_topology()

        result = asyncio.run(
            check_and_load_sde(
                store, settings, build_number=123, zip_path=FIXTURES / "sample-jsonl.zip"
            )
        )

        assert result.skipped is False
        assert store.has_system_topology()
