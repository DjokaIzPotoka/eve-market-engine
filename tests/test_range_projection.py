"""Tests for project_buy_orders: range dispatch extracted from Faza 3's book_features.

Same linear-chain (A-B-C-D) topology as test_book_features.py, but exercised directly and
checked at order-level granularity (order_id, price, volume_remain per covered station).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from eve.features.range_projection import ProjectedBuyOrder, project_buy_orders
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 90000001
SYS_A, SYS_B, SYS_C, SYS_D = 90000101, 90000102, 90000103, 90000104
STATION_A, STATION_B, STATION_C, STATION_D = 90000201, 90000202, 90000203, 90000204
TYPE_ID = 34


def _seed_topology(store: DuckDBStore) -> None:
    for system_id in (SYS_A, SYS_B, SYS_C, SYS_D):
        store._con.execute(
            "INSERT INTO solar_systems VALUES (?, ?, ?, ?, ?)",
            [system_id, REGION_ID, 1, 0.5, f"sys-{system_id}"],
        )
    for station_id, system_id in (
        (STATION_A, SYS_A),
        (STATION_B, SYS_B),
        (STATION_C, SYS_C),
        (STATION_D, SYS_D),
    ):
        store._con.execute(
            "INSERT INTO locations VALUES (?, 'station', ?, ?)",
            [station_id, f"station-{station_id}", system_id],
        )
    # linear chain A - B - C - D
    for a, b in ((SYS_A, SYS_B), (SYS_B, SYS_C), (SYS_C, SYS_D)):
        store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [a, b])
        store._con.execute("INSERT INTO system_jumps VALUES (?, ?)", [b, a])


def _insert_buy_order(
    store: DuckDBStore,
    order_id: int,
    *,
    location_id: int,
    system_id: int,
    price: float,
    volume: int,
    range_: str,
    min_volume: int = 1,
    type_id: int = TYPE_ID,
) -> None:
    store._con.execute(
        """
        INSERT INTO orders_latest
        (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
         volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
        VALUES (?, ?, ?, ?, true, ?, ?, ?, ?, 90, ?, ?, 'snap-1', ?)
        """,
        [
            order_id,
            type_id,
            location_id,
            system_id,
            price,
            volume,
            volume,
            min_volume,
            range_,
            REGION_ID,
            datetime(2026, 9, 20, tzinfo=UTC).replace(tzinfo=None),
        ],
    )


def test_station_range_covers_only_its_own_station(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_topology(store)
        _insert_buy_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, price=5, volume=10, range_="station"
        )
        rows = project_buy_orders(store, REGION_ID)

    assert rows == [
        ProjectedBuyOrder(
            order_id=1, type_id=TYPE_ID, location_id=STATION_A, price=5, volume_remain=10
        )
    ]


def test_region_range_covers_every_station_in_region(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_topology(store)
        _insert_buy_order(
            store, 1, location_id=STATION_C, system_id=SYS_C, price=3, volume=30, range_="region"
        )
        rows = project_buy_orders(store, REGION_ID)

    covered = {row.location_id for row in rows}
    assert covered == {STATION_A, STATION_B, STATION_C, STATION_D}
    assert all(row.order_id == 1 and row.price == 3 and row.volume_remain == 30 for row in rows)


def test_n_jump_range_uses_bfs(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_topology(store)
        _insert_buy_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, price=4, volume=20, range_="1"
        )
        rows = project_buy_orders(store, REGION_ID)

    covered = {row.location_id for row in rows}
    assert covered == {STATION_A, STATION_B}  # 1 jump from A reaches B, not C/D


def test_location_ids_filters_output_but_not_coverage(tmp_path: Path) -> None:
    """Restricting to one station still picks up a region-range order placed elsewhere."""
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_topology(store)
        _insert_buy_order(
            store, 1, location_id=STATION_C, system_id=SYS_C, price=6, volume=1, range_="region"
        )
        rows = project_buy_orders(store, REGION_ID, location_ids=[STATION_B])

    assert rows == [
        ProjectedBuyOrder(
            order_id=1, type_id=TYPE_ID, location_id=STATION_B, price=6, volume_remain=1
        )
    ]


def test_min_volume_scam_orders_are_excluded(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_topology(store)
        _insert_buy_order(
            store,
            1,
            location_id=STATION_A,
            system_id=SYS_A,
            price=100,
            volume=5,
            range_="station",
            min_volume=50,
        )
        rows = project_buy_orders(store, REGION_ID)

    assert rows == []


def test_unknown_range_value_is_skipped(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_topology(store)
        _insert_buy_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, price=5, volume=10, range_="freighter"
        )
        rows = project_buy_orders(store, REGION_ID)

    assert rows == []
