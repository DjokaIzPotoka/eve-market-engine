"""Tests for compute_book_features: range projection, scam filter, depth, history deviation.

Uses a small hand-built topology (linear chain A-B-C-D) seeded directly into a DuckDBStore,
covering all four `range` cases against a real BFS instead of a full SDE fixture.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from eve.config import (
    Budget,
    Cargo,
    Esi,
    Features,
    Filters,
    Settings,
    Skills,
    Standings,
    StationPair,
    Storage,
)
from eve.features.book_features import compute_book_features
from eve.store.duckdb_store import DuckDBStore

REGION_ID = 90000001
SYS_A, SYS_B, SYS_C, SYS_D = 90000101, 90000102, 90000103, 90000104
STATION_A, STATION_B, STATION_C, STATION_D = 90000201, 90000202, 90000203, 90000204
TYPE_ID = 34


def _settings(tmp_path: Path, *, depth_levels: int = 2, near_top_pct: float = 0.3) -> Settings:
    return Settings.model_construct(
        skills=Skills(),
        standings=Standings(),
        budget=Budget(default_isk=1),
        cargo=Cargo(default_m3=1),
        station_pairs=[StationPair(from_station_id=1, to_station_id=2)],
        filters=Filters(),
        esi=Esi(contact="test@example.com"),
        storage=Storage(duckdb_path=str(tmp_path / "eve.duckdb")),
        features=Features(depth_levels=depth_levels, near_top_pct=near_top_pct),
    )


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


def _insert_order(
    store: DuckDBStore,
    order_id: int,
    *,
    location_id: int,
    system_id: int,
    is_buy: bool,
    price: float,
    volume: int,
    min_volume: int = 1,
    range_: str = "station",
    type_id: int = TYPE_ID,
) -> None:
    store._con.execute(
        """
        INSERT INTO orders_latest
        (order_id, type_id, location_id, system_id, is_buy_order, price, volume_total,
         volume_remain, min_volume, duration, "range", region_id, snapshot_id, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 90, ?, ?, 'snap-1', ?)
        """,
        [
            order_id,
            type_id,
            location_id,
            system_id,
            is_buy,
            price,
            volume,
            volume,
            min_volume,
            range_,
            REGION_ID,
            datetime(2026, 9, 20, tzinfo=UTC).replace(tzinfo=None),
        ],
    )


def _seed_snapshot(store: DuckDBStore, fetched_at: datetime) -> None:
    store._con.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
        [
            "snap-1",
            REGION_ID,
            fetched_at.replace(tzinfo=None),
            (fetched_at + timedelta(minutes=5)).replace(tzinfo=None),
            1,
        ],
    )


def test_compute_book_features_range_projection_and_scam_filter(tmp_path: Path) -> None:
    settings = _settings(tmp_path, depth_levels=2, near_top_pct=0.3)
    fetched_at = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, fetched_at)

        # sell side at station A: three price levels
        _insert_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, is_buy=False, price=10, volume=100
        )
        _insert_order(
            store, 2, location_id=STATION_A, system_id=SYS_A, is_buy=False, price=11, volume=50
        )
        _insert_order(
            store, 3, location_id=STATION_A, system_id=SYS_A, is_buy=False, price=12, volume=20
        )

        # buy side: station range (covers A only)
        _insert_order(
            store,
            4,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=True,
            price=5,
            volume=10,
            range_="station",
        )
        # buy side: 1-jump range from A (covers A, B -- not C, D)
        _insert_order(
            store,
            5,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=True,
            price=4,
            volume=20,
            range_="1",
        )
        # buy side: region range (covers A, B, C, D)
        _insert_order(
            store,
            6,
            location_id=STATION_C,
            system_id=SYS_C,
            is_buy=True,
            price=3,
            volume=30,
            range_="region",
        )
        # scam: absurdly high price but min_volume > 1 -- must be excluded entirely
        _insert_order(
            store,
            7,
            location_id=STATION_A,
            system_id=SYS_A,
            is_buy=True,
            price=100,
            volume=5,
            min_volume=50,
            range_="station",
        )

        n = compute_book_features(store, REGION_ID, settings)
        rows = {
            (loc, typ): row
            for loc, typ, *row in store._con.execute(
                "SELECT location_id, type_id, best_bid, best_ask, bid_depth_volumes, "
                "ask_depth_volumes, bid_orders_near_top, ask_orders_near_top "
                "FROM book_features"
            ).fetchall()
        }

    assert n == len(rows)

    a_bid, a_ask, a_bid_depth, a_ask_depth, a_bid_near, a_ask_near = rows[(STATION_A, TYPE_ID)]
    assert a_bid == 5  # NOT 100 -- the min_volume>1 scam order must not win
    assert a_ask == 10
    assert a_bid_depth == [10, 20]  # price 5 (vol 10), then price 4 (vol 20)
    assert a_ask_depth == [100, 50]
    assert a_bid_near == 2  # 5 and 4 are within 30% of 5; 3 is not (3 < 3.5)
    assert a_ask_near == 3  # all three asks within 30% of 10 (<=13)

    b_bid, b_ask, *_ = rows[(STATION_B, TYPE_ID)]
    assert b_bid == 4  # covered by the 1-jump order and the region order, not the station one
    assert b_ask is None  # no sell orders at B

    c_bid, *_ = rows[(STATION_C, TYPE_ID)]
    assert c_bid == 3  # only the region-range order reaches C (1-jump order does not)

    d_bid, *_ = rows[(STATION_D, TYPE_ID)]
    assert d_bid == 3  # only the region-range order reaches D

    assert (STATION_A, TYPE_ID) in rows
    # the scam order alone must never produce a row anywhere by itself being the only order


def test_compute_book_features_location_ids_filters_output_but_not_coverage(
    tmp_path: Path,
) -> None:
    """Restricting to one station still picks up a region-range order placed elsewhere."""
    settings = _settings(tmp_path)
    fetched_at = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, fetched_at)
        _insert_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, is_buy=False, price=10, volume=1
        )
        # region-range buy order placed at C must still cover B when we only ask for B
        _insert_order(
            store,
            2,
            location_id=STATION_C,
            system_id=SYS_C,
            is_buy=True,
            price=6,
            volume=1,
            range_="region",
        )

        n = compute_book_features(store, REGION_ID, settings, location_ids=[STATION_B])

        rows = store._con.execute("SELECT location_id, best_bid FROM book_features").fetchall()

    assert n == 1
    assert rows == [(STATION_B, 6)]


def test_compute_book_features_history_deviation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fetched_at = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, fetched_at)
        _insert_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, is_buy=False, price=10, volume=1
        )
        _insert_order(
            store, 2, location_id=STATION_A, system_id=SYS_A, is_buy=True, price=8, volume=1
        )

        for days_ago in range(1, 31):
            store.write_history(
                REGION_ID,
                TYPE_ID,
                date(2026, 9, 20) - timedelta(days=days_ago),
                [
                    {
                        "date": (date(2026, 9, 20) - timedelta(days=days_ago)).isoformat(),
                        "average": 9.0,
                        "highest": 9.0,
                        "lowest": 9.0,
                        "order_count": 1,
                        "volume": 1,
                    }
                ],
            )

        compute_book_features(store, REGION_ID, settings)

        row = store._con.execute(
            "SELECT history_avg_7d, history_avg_30d, price_deviation_30d_pct "
            "FROM book_features WHERE location_id = ? AND type_id = ?",
            [STATION_A, TYPE_ID],
        ).fetchone()

    avg_7d, avg_30d, deviation = row
    assert avg_7d == 9.0
    assert avg_30d == 9.0
    # mid price = (8 + 10) / 2 = 9.0 -- exactly matches history, so 0% deviation
    assert deviation == 0.0


def test_compute_book_features_no_history_gives_null_deviation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    fetched_at = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    with DuckDBStore(settings.storage.duckdb_path) as store:
        _seed_topology(store)
        _seed_snapshot(store, fetched_at)
        _insert_order(
            store, 1, location_id=STATION_A, system_id=SYS_A, is_buy=False, price=10, volume=1
        )

        compute_book_features(store, REGION_ID, settings)

        row = store._con.execute(
            "SELECT history_avg_7d, history_avg_30d, price_deviation_30d_pct "
            "FROM book_features WHERE location_id = ? AND type_id = ?",
            [STATION_A, TYPE_ID],
        ).fetchone()

    assert row == (None, None, None)
