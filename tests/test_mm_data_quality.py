"""Market-making data quality: placeholder quotes are rejected, and fill/relist rates come
from a trailing window of snapshot pairs rather than the single latest 10-minute pair."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from test_market_making import (
    FETCHED_AT,
    REGION_ID,
    STATION_A,
    _seed_book_features,
    _seed_profitable_type,
    _seed_snapshot,
    _seed_station,
    _settings,
)

from eve.market_making.candidates import load_candidates
from eve.store.duckdb_store import DuckDBStore


def _seed_pair(
    store: DuckDBStore,
    type_id: int,
    *,
    to_offset_min: float,
    elapsed_h: float,
    bid_vph: float,
    ask_vph: float,
    relists: float = 0.0,
) -> None:
    to_at = (FETCHED_AT + timedelta(minutes=to_offset_min)).replace(tzinfo=None)
    from_at = to_at - timedelta(hours=elapsed_h)
    tag = str(to_offset_min)  # one snapshot pair per timestamp, shared by all types
    store._con.execute(
        """
        INSERT INTO book_diff_metrics
        (region_id, location_id, type_id, from_snapshot_id, to_snapshot_id, from_fetched_at,
         to_fetched_at, elapsed_hours, bid_volume_per_hour, ask_volume_per_hour,
         relists_per_hour, bid_competitor_count, ask_competitor_count,
         estimated_bid_capture_rate, estimated_ask_capture_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0.5, 0.5)
        """,
        [
            REGION_ID,
            STATION_A,
            type_id,
            f"from-{tag}",
            f"to-{tag}",
            from_at,
            to_at,
            elapsed_h,
            bid_vph,
            ask_vph,
            relists,
        ],
    )


def _seed_book(store: DuckDBStore, type_id: int, bid: float, ask: float, avg: float) -> None:
    _seed_book_features(
        store,
        "snap-1",
        type_id,
        best_bid=bid,
        best_ask=ask,
        bid_near_top=3,
        ask_near_top=3,
        history_avg_30d=avg,
    )
    _seed_pair(store, type_id, to_offset_min=0, elapsed_h=1.0, bid_vph=2.0, ask_vph=2.0)


def test_quote_far_from_average_price_is_rejected(tmp_path: Path) -> None:
    OK, JUNK_BID, JUNK_ASK = 91, 92, 93
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)
        _seed_book(store, OK, bid=1000.0, ask=1600.0, avg=1300.0)  # both within 25% of avg
        _seed_book(store, JUNK_BID, bid=1.03, ask=1600.0, avg=1300.0)  # placeholder bid
        _seed_book(store, JUNK_ASK, bid=1000.0, ask=39229.0, avg=1300.0)  # placeholder ask

        found = {c.type_id for c in load_candidates(store, STATION_A, 1e9, _settings())}

    assert found == {OK}


def test_deviation_limit_is_configurable(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)
        _seed_book(store, 91, bid=500.0, ask=1600.0, avg=1300.0)  # bid is 62% under the average

        strict = load_candidates(store, STATION_A, 1e9, _settings())
        loose = load_candidates(store, STATION_A, 1e9, _settings(max_price_deviation_pct=80.0))

    assert strict == []
    assert [c.type_id for c in loose] == [91]


def test_diff_metrics_average_over_window_weighted_by_elapsed_time(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        # 1h at 6/h, then 3h at 2/h -> (6*1 + 2*3) / 4 = 3/h; the latest pair alone says 2/h.
        _seed_pair(store, 1, to_offset_min=-180, elapsed_h=1.0, bid_vph=6.0, ask_vph=0.0)
        _seed_pair(store, 1, to_offset_min=0, elapsed_h=3.0, bid_vph=2.0, ask_vph=0.0)

        (latest,) = store.latest_book_diff_metrics(STATION_A)
        (windowed,) = store.latest_book_diff_metrics(STATION_A, window_hours=24)

    assert latest[1] == 2.0
    assert windowed[1] == 3.0


def test_quiet_intervals_dilute_the_rate(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        # Type 1 only moved in the 1h pair; type 2 anchors a further 3h of the window in which
        # type 1 has no metrics row (no events). 6/h for 1h out of 4h is 1.5/h, not 6/h.
        _seed_pair(store, 1, to_offset_min=-180, elapsed_h=1.0, bid_vph=6.0, ask_vph=0.0)
        _seed_pair(store, 2, to_offset_min=-180, elapsed_h=1.0, bid_vph=1.0, ask_vph=0.0)
        _seed_pair(store, 2, to_offset_min=0, elapsed_h=3.0, bid_vph=1.0, ask_vph=0.0)

        rows = {r[0]: r for r in store.latest_book_diff_metrics(STATION_A, window_hours=24)}

    assert rows[1][1] == 1.5


def test_diff_metrics_window_excludes_old_pairs_and_gap_pairs(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_pair(store, 1, to_offset_min=-60 * 30, elapsed_h=0.1, bid_vph=100.0, ask_vph=0.0)
        _seed_pair(store, 1, to_offset_min=-60 * 5, elapsed_h=4.0, bid_vph=50.0, ask_vph=0.0)
        _seed_pair(store, 1, to_offset_min=0, elapsed_h=0.5, bid_vph=4.0, ask_vph=0.0)

        rows = store.latest_book_diff_metrics(STATION_A, window_hours=24, max_gap_hours=1.0)

    # The 30h-old pair is outside the window; the 4h pair spans a data gap (> 1h): dropped.
    assert [r[1] for r in rows] == [4.0]


def test_candidates_use_windowed_rates(tmp_path: Path) -> None:
    with DuckDBStore(tmp_path / "eve.duckdb") as store:
        _seed_station(store)
        _seed_snapshot(store, "snap-1", FETCHED_AT)
        _seed_profitable_type(store, 81, relists_per_hour=0.02)  # 2.0/h at "from-1"/"to-1"
        # An earlier busy hour lifts the window average above the latest pair's rate.
        _seed_pair(store, 81, to_offset_min=-30, elapsed_h=0.5, bid_vph=10.0, ask_vph=10.0)

        (windowed,) = load_candidates(store, STATION_A, 1e9, _settings())
        narrow = _settings()
        narrow.fill.metrics_window_hours = 0.001  # only the newest pair
        (latest_only,) = load_candidates(store, STATION_A, 1e9, narrow)

    assert windowed.expected_daily_fill > latest_only.expected_daily_fill
