"""DuckDB-backed storage for order snapshots."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl


def _to_naive_utc(dt: datetime) -> datetime:
    """Strip tzinfo for storage; callers must already be passing UTC timestamps."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _to_aware_utc(dt: datetime) -> datetime:
    """Reattach UTC tzinfo -- duckdb returns naive datetimes for TIMESTAMP columns."""
    return dt.replace(tzinfo=UTC)


SNAPSHOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    region_id BIGINT NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    order_count BIGINT NOT NULL
)
"""

ORDERS_LATEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders_latest (
    order_id BIGINT,
    type_id BIGINT,
    location_id BIGINT,
    system_id BIGINT,
    is_buy_order BOOLEAN,
    price DOUBLE,
    volume_total BIGINT,
    volume_remain BIGINT,
    min_volume BIGINT,
    duration INTEGER,
    issued TIMESTAMP,
    "range" TEXT,
    region_id BIGINT,
    snapshot_id TEXT,
    fetched_at TIMESTAMP
)
"""

ORDERS_LATEST_COLUMNS = [
    "order_id",
    "type_id",
    "location_id",
    "system_id",
    "is_buy_order",
    "price",
    "volume_total",
    "volume_remain",
    "min_volume",
    "duration",
    "issued",
    '"range"',
    "region_id",
    "snapshot_id",
    "fetched_at",
]


TYPES_SCHEMA = """
CREATE TABLE IF NOT EXISTS types (
    type_id BIGINT PRIMARY KEY,
    name TEXT,
    packaged_volume DOUBLE,
    market_group_id BIGINT,
    published BOOLEAN
)
"""

LOCATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    location_id BIGINT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT,
    system_id BIGINT
)
"""

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    region_id BIGINT,
    type_id BIGINT,
    date DATE,
    average DOUBLE,
    highest DOUBLE,
    lowest DOUBLE,
    order_count BIGINT,
    volume BIGINT,
    PRIMARY KEY (region_id, type_id, date)
)
"""

HISTORY_FETCH_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS history_fetch_log (
    region_id BIGINT,
    type_id BIGINT,
    fetch_date DATE,
    fetched_at TIMESTAMP,
    PRIMARY KEY (region_id, type_id, fetch_date)
)
"""

SDE_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS sde_meta (
    key TEXT PRIMARY KEY,
    build_number BIGINT NOT NULL,
    loaded_at TIMESTAMP NOT NULL
)
"""

SOLAR_SYSTEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS solar_systems (
    system_id BIGINT PRIMARY KEY,
    region_id BIGINT,
    constellation_id BIGINT,
    security_status DOUBLE,
    name TEXT
)
"""

SYSTEM_JUMPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS system_jumps (
    from_system_id BIGINT,
    to_system_id BIGINT,
    PRIMARY KEY (from_system_id, to_system_id)
)
"""

BOOK_FEATURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_features (
    snapshot_id TEXT,
    location_id BIGINT,
    type_id BIGINT,
    best_bid DOUBLE,
    best_ask DOUBLE,
    bid_depth_volumes BIGINT[],
    ask_depth_volumes BIGINT[],
    bid_orders_near_top INTEGER,
    ask_orders_near_top INTEGER,
    history_avg_7d DOUBLE,
    history_avg_30d DOUBLE,
    price_deviation_30d_pct DOUBLE,
    computed_at TIMESTAMP,
    PRIMARY KEY (snapshot_id, location_id, type_id)
)
"""

SNAPSHOT_DIFF_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_diff_events (
    region_id BIGINT,
    location_id BIGINT,
    type_id BIGINT,
    order_id BIGINT,
    is_buy_order BOOLEAN,
    event_type TEXT,
    price DOUBLE,
    filled_volume BIGINT,
    from_snapshot_id TEXT,
    to_snapshot_id TEXT,
    from_fetched_at TIMESTAMP,
    to_fetched_at TIMESTAMP,
    PRIMARY KEY (from_snapshot_id, to_snapshot_id, order_id, event_type)
)
"""

BOOK_DIFF_METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_diff_metrics (
    region_id BIGINT,
    location_id BIGINT,
    type_id BIGINT,
    from_snapshot_id TEXT,
    to_snapshot_id TEXT,
    from_fetched_at TIMESTAMP,
    to_fetched_at TIMESTAMP,
    elapsed_hours DOUBLE,
    bid_volume_per_hour DOUBLE,
    ask_volume_per_hour DOUBLE,
    relists_per_hour DOUBLE,
    bid_competitor_count INTEGER,
    ask_competitor_count INTEGER,
    estimated_bid_capture_rate DOUBLE,
    estimated_ask_capture_rate DOUBLE,
    PRIMARY KEY (location_id, type_id, from_snapshot_id, to_snapshot_id)
)
"""

INGEST_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    region_id BIGINT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP NOT NULL,
    success BOOLEAN NOT NULL,
    order_count BIGINT,
    error_message TEXT
)
"""

PLAN_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    from_station_id BIGINT NOT NULL,
    to_station_id BIGINT,
    total_expected_profit DOUBLE NOT NULL,
    expected_profit_unit TEXT NOT NULL
)
"""

PLAN_LINES_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_lines (
    run_id TEXT NOT NULL,
    type_id BIGINT NOT NULL,
    type_name TEXT,
    suggested_quantity DOUBLE NOT NULL,
    suggested_buy_price DOUBLE NOT NULL,
    suggested_sell_price DOUBLE NOT NULL,
    PRIMARY KEY (run_id, type_id)
)
"""

TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    run_id TEXT,
    type_id BIGINT NOT NULL,
    logged_at TIMESTAMP NOT NULL,
    quantity DOUBLE NOT NULL,
    actual_buy_price DOUBLE NOT NULL,
    actual_sell_price DOUBLE NOT NULL,
    notes TEXT
)
"""

ESI_LIMIT_SAMPLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS esi_limit_samples (
    sampled_at TIMESTAMP PRIMARY KEY,
    remain INTEGER NOT NULL,
    reset_s DOUBLE NOT NULL
)
"""

DISK_SAMPLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS disk_samples (
    sampled_at TIMESTAMP PRIMARY KEY,
    used_bytes BIGINT NOT NULL,
    total_bytes BIGINT NOT NULL
)
"""

# Structure IDs (player-owned citizen structures) are far above any NPC station ID and
# require authenticated ESI -- out of scope for v1 (see CLAUDE.md "Kasnije (van v1)").
MAX_NPC_STATION_ID = 70_000_000


@dataclass
class SnapshotMeta:
    snapshot_id: str
    region_id: int
    fetched_at: datetime
    expires_at: datetime
    order_count: int


class DuckDBStore:
    """Owns the duckdb connection for order snapshots (`snapshots` + `orders_latest` tables)."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self._db_path))
        # duckdb defaults the session TimeZone to the OS locale, which would otherwise silently
        # shift tz-aware timestamp columns (e.g. parquet's `issued`, `fetched_at`) to local wall
        # time on read_parquet/insert. Everything in this store is UTC.
        self._con.execute("SET TimeZone='UTC'")
        self._con.execute(SNAPSHOTS_SCHEMA)
        self._con.execute(ORDERS_LATEST_SCHEMA)
        self._con.execute(TYPES_SCHEMA)
        self._con.execute(LOCATIONS_SCHEMA)
        self._con.execute(HISTORY_SCHEMA)
        self._con.execute(HISTORY_FETCH_LOG_SCHEMA)
        self._con.execute(SDE_META_SCHEMA)
        self._con.execute(SOLAR_SYSTEMS_SCHEMA)
        self._con.execute(SYSTEM_JUMPS_SCHEMA)
        self._con.execute(BOOK_FEATURES_SCHEMA)
        self._con.execute(SNAPSHOT_DIFF_EVENTS_SCHEMA)
        self._con.execute(BOOK_DIFF_METRICS_SCHEMA)
        self._con.execute(INGEST_RUNS_SCHEMA)
        self._con.execute(PLAN_RUNS_SCHEMA)
        self._con.execute(PLAN_LINES_SCHEMA)
        self._con.execute(TRADES_SCHEMA)
        self._con.execute(ESI_LIMIT_SAMPLES_SCHEMA)
        self._con.execute(DISK_SAMPLES_SCHEMA)

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> DuckDBStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def latest_snapshot_meta(self, region_id: int) -> SnapshotMeta | None:
        row = self._con.execute(
            """
            SELECT snapshot_id, region_id, fetched_at, expires_at, order_count
            FROM snapshots
            WHERE region_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            [region_id],
        ).fetchone()
        if row is None:
            return None
        snapshot_id, region_id, fetched_at, expires_at, order_count = row
        return SnapshotMeta(
            snapshot_id=snapshot_id,
            region_id=region_id,
            fetched_at=_to_aware_utc(fetched_at),
            expires_at=_to_aware_utc(expires_at),
            order_count=order_count,
        )

    def snapshot_history(self, region_id: int) -> list[SnapshotMeta]:
        """Every snapshot ever ingested for `region_id`, oldest first (append-only table)."""
        rows = self._con.execute(
            """
            SELECT snapshot_id, region_id, fetched_at, expires_at, order_count
            FROM snapshots
            WHERE region_id = ?
            ORDER BY fetched_at ASC
            """,
            [region_id],
        ).fetchall()
        return [
            SnapshotMeta(
                snapshot_id=snapshot_id,
                region_id=region_id,
                fetched_at=_to_aware_utc(fetched_at),
                expires_at=_to_aware_utc(expires_at),
                order_count=order_count,
            )
            for snapshot_id, region_id, fetched_at, expires_at, order_count in rows
        ]

    def write_snapshot(
        self,
        region_id: int,
        snapshot_id: str,
        fetched_at: datetime,
        expires_at: datetime,
        order_count: int,
        parquet_path: str | Path,
    ) -> None:
        """Record snapshot metadata and replace `orders_latest` rows for this region.

        Reads straight from the raw Parquet file (duckdb's built-in `read_parquet`) so
        `orders_latest` always matches what was persisted to disk, with no extra
        DataFrame<->duckdb interop layer (e.g. pyarrow) needed.
        """
        columns_sql = ", ".join(ORDERS_LATEST_COLUMNS)
        self._con.execute("BEGIN TRANSACTION")
        try:
            self._con.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
                [
                    snapshot_id,
                    region_id,
                    _to_naive_utc(fetched_at),
                    _to_naive_utc(expires_at),
                    order_count,
                ],
            )
            self._con.execute("DELETE FROM orders_latest WHERE region_id = ?", [region_id])
            self._con.execute(
                f"INSERT INTO orders_latest ({columns_sql}) "
                f"SELECT {columns_sql} FROM read_parquet(?)",
                [str(parquet_path)],
            )
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")

    def history_averages(
        self, region_id: int, as_of: date
    ) -> dict[int, tuple[float | None, float | None]]:
        """`type_id -> (avg_price_7d, avg_price_30d)` using history strictly before `as_of`."""
        rows = self._con.execute(
            """
            SELECT
                type_id,
                AVG(average) FILTER (WHERE date >= ? - INTERVAL 7 DAY) AS avg_7d,
                AVG(average) FILTER (WHERE date >= ? - INTERVAL 30 DAY) AS avg_30d
            FROM history
            WHERE region_id = ? AND date < ?
            GROUP BY type_id
            """,
            [as_of, as_of, region_id, as_of],
        ).fetchall()
        return {type_id: (avg_7d, avg_30d) for type_id, avg_7d, avg_30d in rows}

    def history_volume_map(self, region_id: int, target_date: date) -> dict[int, float]:
        """`type_id -> volume` traded region-wide on `target_date`, per ESI history."""
        rows = self._con.execute(
            "SELECT type_id, volume FROM history WHERE region_id = ? AND date = ?",
            [region_id, target_date],
        ).fetchall()
        return dict(rows)

    def history_stats(
        self, region_id: int, before_date: date, window_days: int
    ) -> dict[int, tuple[float, float, float]]:
        """`type_id -> (avg_daily_volume, volatility_pct, avg_price)` over the `window_days`
        days strictly before `before_date`.

        `volatility_pct` = mean intraday range (high - low) / average, plus the day-to-day
        spread of the daily average price (population stddev / mean). The second term matters
        for thin items whose price jumps *between* days while each day is a single trade
        (intraday range 0)."""
        rows = self._con.execute(
            """
            SELECT
                type_id,
                AVG(volume),
                COALESCE(AVG(CASE WHEN average > 0 THEN (highest - lowest) / average * 100 END), 0)
                    + COALESCE(STDDEV_POP(average) / NULLIF(AVG(average), 0) * 100, 0),
                AVG(average)
            FROM history
            WHERE region_id = ? AND date < ? AND date >= ?
            GROUP BY type_id
            """,
            [region_id, before_date, before_date - timedelta(days=window_days)],
        ).fetchall()
        return {t: (vol or 0.0, vola or 0.0, price or 0.0) for t, vol, vola, price in rows}

    def sell_orders(self, region_id: int) -> list[tuple[int, int, float, int]]:
        """`(location_id, type_id, price, volume_remain)` for every sell order in the region."""
        return self._con.execute(
            "SELECT location_id, type_id, price, volume_remain FROM orders_latest "
            "WHERE region_id = ? AND is_buy_order = false",
            [region_id],
        ).fetchall()

    def sell_orders_at_location(self, location_id: int) -> list[tuple[int, int, float, int]]:
        """`(order_id, type_id, price, volume_remain)` for sell orders at exactly this station.

        Sell orders have no `range` -- a station's own listings are the only source for
        both the purchase book (buying at the origin) and the listing reference price
        (the current best ask at a destination), so no `region_id` scoping is needed.
        """
        return self._con.execute(
            "SELECT order_id, type_id, price, volume_remain FROM orders_latest "
            "WHERE location_id = ? AND is_buy_order = false",
            [location_id],
        ).fetchall()

    def types_by_id(self, type_ids: list[int] | None = None) -> dict[int, tuple[str, float]]:
        """`type_id -> (name, packaged_volume)`, optionally scoped to `type_ids`."""
        # Always read the whole table and filter in Python: binding a large Python list as a
        # parameter costs a slow per-element conversion in duckdb (~40 s for 17k ids on Windows).
        rows = self._con.execute("SELECT type_id, name, packaged_volume FROM types").fetchall()
        if type_ids is not None:
            wanted = set(type_ids)
            rows = [row for row in rows if row[0] in wanted]
        return {type_id: (name, packaged_volume) for type_id, name, packaged_volume in rows}

    def buy_orders_for_projection(
        self, region_id: int
    ) -> list[tuple[int, int, int, int, float, int, str]]:
        """`(order_id, type_id, location_id, system_id, price, volume_remain, range)` for buy
        orders with `min_volume <= 1` -- the "exit liquidity" filter from CLAUDE.md."""
        return self._con.execute(
            'SELECT order_id, type_id, location_id, system_id, price, volume_remain, "range" '
            "FROM orders_latest WHERE region_id = ? AND is_buy_order = true AND min_volume <= 1",
            [region_id],
        ).fetchall()

    # -- Book features --------------------------------------------------------

    def write_book_features(self, snapshot_id: str, rows: list[dict]) -> None:
        """Replace `book_features` rows for `snapshot_id` (idempotent on re-run).

        Writes via a temp Parquet file + `read_parquet`, not row-by-row `execute`/
        `executemany` -- duckdb's python binding for LIST-typed params (the bid/ask depth
        arrays) is roughly 1000x slower per row than scalar params (~7ms/row measured),
        which is impractical at the row counts a liquid region produces.
        """
        self._con.execute("BEGIN TRANSACTION")
        try:
            self._con.execute("DELETE FROM book_features WHERE snapshot_id = ?", [snapshot_id])
            if rows:
                computed_at = _to_naive_utc(datetime.now(UTC))
                df = pl.DataFrame(
                    {
                        "snapshot_id": [snapshot_id] * len(rows),
                        "location_id": [r["location_id"] for r in rows],
                        "type_id": [r["type_id"] for r in rows],
                        "best_bid": [r["best_bid"] for r in rows],
                        "best_ask": [r["best_ask"] for r in rows],
                        "bid_depth_volumes": [r["bid_depth_volumes"] for r in rows],
                        "ask_depth_volumes": [r["ask_depth_volumes"] for r in rows],
                        "bid_orders_near_top": [r["bid_orders_near_top"] for r in rows],
                        "ask_orders_near_top": [r["ask_orders_near_top"] for r in rows],
                        "history_avg_7d": [r["history_avg_7d"] for r in rows],
                        "history_avg_30d": [r["history_avg_30d"] for r in rows],
                        "price_deviation_30d_pct": [r["price_deviation_30d_pct"] for r in rows],
                        "computed_at": [computed_at] * len(rows),
                    },
                    schema_overrides={
                        "bid_depth_volumes": pl.List(pl.Int64),
                        "ask_depth_volumes": pl.List(pl.Int64),
                    },
                )
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_path = Path(tmp_dir) / "book_features.parquet"
                    df.write_parquet(tmp_path)
                    self._con.execute(
                        "INSERT INTO book_features SELECT * FROM read_parquet(?)",
                        [str(tmp_path)],
                    )
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")

    def latest_book_features(self, region_id: int, location_id: int) -> list[tuple]:
        """One row per `type_id`: the most recent `book_features` snapshot for this
        location. `book_features` accumulates one row per historical snapshot (nothing
        deletes old ones), so "latest" has to be picked explicitly."""
        return self._con.execute(
            """
            SELECT bf.type_id, bf.best_bid, bf.best_ask, bf.bid_orders_near_top,
                   bf.ask_orders_near_top, bf.history_avg_7d, bf.history_avg_30d,
                   bf.price_deviation_30d_pct
            FROM book_features bf
            JOIN snapshots s ON s.snapshot_id = bf.snapshot_id
            WHERE bf.location_id = ? AND s.region_id = ?
            QUALIFY ROW_NUMBER() OVER (PARTITION BY bf.type_id ORDER BY s.fetched_at DESC) = 1
            """,
            [location_id, region_id],
        ).fetchall()

    def latest_book_features_for_type(
        self, region_id: int, location_id: int, type_id: int
    ) -> tuple | None:
        """Same columns as `latest_book_features`, filtered to one `type_id` in SQL --
        for a single-item view where fetching every type at the location would be wasteful."""
        return self._con.execute(
            """
            SELECT bf.type_id, bf.best_bid, bf.best_ask, bf.bid_orders_near_top,
                   bf.ask_orders_near_top, bf.history_avg_7d, bf.history_avg_30d,
                   bf.price_deviation_30d_pct
            FROM book_features bf
            JOIN snapshots s ON s.snapshot_id = bf.snapshot_id
            WHERE bf.location_id = ? AND s.region_id = ? AND bf.type_id = ?
            QUALIFY ROW_NUMBER() OVER (PARTITION BY bf.type_id ORDER BY s.fetched_at DESC) = 1
            """,
            [location_id, region_id, type_id],
        ).fetchone()

    def search_types_by_name(self, query: str, limit: int = 20) -> list[tuple[int, str]]:
        """`(type_id, name)` for published types whose name contains `query`
        (case-insensitive), name-sorted -- backs the web dashboard's item picker."""
        return self._con.execute(
            """
            SELECT type_id, name FROM types
            WHERE published = true AND name ILIKE '%' || ? || '%'
            ORDER BY name
            LIMIT ?
            """,
            [query, limit],
        ).fetchall()

    def latest_book_diff_metrics(
        self,
        location_id: int,
        window_hours: float | None = None,
        max_gap_hours: float | None = None,
    ) -> list[tuple]:
        """One row per `type_id`:
        `(type_id, bid_volume_per_hour, ask_volume_per_hour, relists_per_hour,
        bid_competitor_count, ask_competitor_count, estimated_bid_capture_rate,
        estimated_ask_capture_rate)`.

        Like `book_features`, `book_diff_metrics` accumulates one row per historical
        snapshot pair. Without `window_hours` the single most recent pair is returned. With
        it, rates are averaged over the pairs ending within `window_hours` of the newest
        one (time-weighted; competitor counts stay the latest value). Pairs longer than
        `max_gap_hours` are skipped: across a data gap the fill model can't tell fills from
        cancels, so those intervals carry no rate information.
        """
        if window_hours is None:
            return self._con.execute(
                """
                SELECT type_id, bid_volume_per_hour, ask_volume_per_hour, relists_per_hour,
                       bid_competitor_count, ask_competitor_count,
                       estimated_bid_capture_rate, estimated_ask_capture_rate
                FROM book_diff_metrics
                WHERE location_id = ?
                QUALIFY ROW_NUMBER() OVER (PARTITION BY type_id ORDER BY to_fetched_at DESC) = 1
                """,
                [location_id],
            ).fetchall()
        # Metrics rows exist only for (location, type) pairs that had a diff event in that
        # interval, so the rate denominator must be the whole window's elapsed time (every
        # pair in the region), not just the intervals in which this type happened to move.
        return self._con.execute(
            """
            WITH newest AS (
                SELECT MAX(to_fetched_at) AS m, ANY_VALUE(region_id) AS r
                FROM book_diff_metrics WHERE location_id = ?
            ),
            usable AS (
                SELECT b.* FROM book_diff_metrics b, newest n
                WHERE b.region_id = n.r
                  AND b.to_fetched_at >= n.m - to_minutes(CAST(? AS BIGINT))
                  AND b.elapsed_hours > 0
                  AND (? IS NULL OR b.elapsed_hours <= ?)
            ),
            total AS (
                SELECT SUM(elapsed_hours) AS h FROM (
                    SELECT DISTINCT from_snapshot_id, to_snapshot_id, elapsed_hours FROM usable
                )
            )
            SELECT type_id,
                   SUM(bid_volume_per_hour * elapsed_hours) / ANY_VALUE(total.h),
                   SUM(ask_volume_per_hour * elapsed_hours) / ANY_VALUE(total.h),
                   SUM(relists_per_hour * elapsed_hours) / ANY_VALUE(total.h),
                   arg_max(bid_competitor_count, to_fetched_at),
                   arg_max(ask_competitor_count, to_fetched_at),
                   SUM(estimated_bid_capture_rate * elapsed_hours) / SUM(elapsed_hours),
                   SUM(estimated_ask_capture_rate * elapsed_hours) / SUM(elapsed_hours)
            FROM usable, total
            WHERE location_id = ?
            GROUP BY type_id
            """,
            [location_id, round(window_hours * 60), max_gap_hours, max_gap_hours, location_id],
        ).fetchall()

    def diffed_pairs(self, region_id: int) -> set[tuple[str, str]]:
        """`(from_snapshot_id, to_snapshot_id)` pairs that already have diff metrics."""
        return {
            (f, t)
            for f, t in self._con.execute(
                "SELECT DISTINCT from_snapshot_id, to_snapshot_id FROM book_diff_metrics "
                "WHERE region_id = ?",
                [region_id],
            ).fetchall()
        }

    def has_book_features(self, snapshot_id: str) -> bool:
        return (
            self._con.execute(
                "SELECT 1 FROM book_features WHERE snapshot_id = ? LIMIT 1", [snapshot_id]
            ).fetchone()
            is not None
        )

    def prune_book_features(self, before: datetime) -> int:
        """Delete `book_features` rows computed before `before`; returns rows removed."""
        cutoff = _to_naive_utc(before)
        (n,) = self._con.execute(
            "SELECT COUNT(*) FROM book_features WHERE computed_at < ?", [cutoff]
        ).fetchone()
        self._con.execute("DELETE FROM book_features WHERE computed_at < ?", [cutoff])
        return n

    # -- SDE types --------------------------------------------------------

    def latest_sde_build(self) -> int | None:
        row = self._con.execute("SELECT build_number FROM sde_meta WHERE key = 'types'").fetchone()
        return row[0] if row is not None else None

    def load_types_from_jsonl(self, jsonl_path: str | Path, build_number: int) -> int:
        """Replace `types` from a `types.jsonl` file and record the SDE build number."""
        self._con.execute("BEGIN TRANSACTION")
        try:
            self._con.execute("DELETE FROM types")
            self._con.execute(
                """
                INSERT INTO types
                SELECT
                    _key AS type_id,
                    name.en AS name,
                    COALESCE(packagedVolume, volume) AS packaged_volume,
                    marketGroupID AS market_group_id,
                    published
                FROM read_json_auto(?, format='newline_delimited')
                """,
                [str(jsonl_path)],
            )
            count = self._con.execute("SELECT COUNT(*) FROM types").fetchone()[0]
            self._con.execute(
                "INSERT OR REPLACE INTO sde_meta VALUES ('types', ?, ?)",
                [build_number, _to_naive_utc(datetime.now(UTC))],
            )
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")
        return count

    def unknown_type_ids(self) -> list[int]:
        """`type_id`s referenced by `orders_latest` with no matching row in `types`."""
        rows = self._con.execute(
            """
            SELECT DISTINCT o.type_id
            FROM orders_latest o
            LEFT JOIN types t ON t.type_id = o.type_id
            WHERE t.type_id IS NULL
            """
        ).fetchall()
        return [r[0] for r in rows]

    def has_system_topology(self) -> bool:
        """Whether `solar_systems`/`system_jumps` have been loaded at least once."""
        row = self._con.execute("SELECT COUNT(*) FROM solar_systems").fetchone()
        return row[0] > 0

    def load_solar_systems_from_jsonl(self, jsonl_path: str | Path) -> int:
        """Replace `solar_systems` from a `mapSolarSystems.jsonl` file."""
        self._con.execute("BEGIN TRANSACTION")
        try:
            self._con.execute("DELETE FROM solar_systems")
            self._con.execute(
                """
                INSERT INTO solar_systems
                SELECT
                    _key AS system_id,
                    regionID AS region_id,
                    constellationID AS constellation_id,
                    securityStatus AS security_status,
                    name.en AS name
                FROM read_json_auto(?, format='newline_delimited')
                """,
                [str(jsonl_path)],
            )
            count = self._con.execute("SELECT COUNT(*) FROM solar_systems").fetchone()[0]
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")
        return count

    def load_system_jumps_from_jsonl(self, jsonl_path: str | Path) -> int:
        """Replace `system_jumps` from a `mapStargates.jsonl` file (one directed edge per gate)."""
        self._con.execute("BEGIN TRANSACTION")
        try:
            self._con.execute("DELETE FROM system_jumps")
            self._con.execute(
                """
                INSERT INTO system_jumps
                SELECT DISTINCT
                    solarSystemID AS from_system_id,
                    destination.solarSystemID AS to_system_id
                FROM read_json_auto(?, format='newline_delimited')
                """,
                [str(jsonl_path)],
            )
            count = self._con.execute("SELECT COUNT(*) FROM system_jumps").fetchone()[0]
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")
        return count

    def jump_graph(self) -> dict[int, set[int]]:
        """The whole system-adjacency graph, for in-memory BFS."""
        graph: dict[int, set[int]] = {}
        for from_id, to_id in self._con.execute(
            "SELECT from_system_id, to_system_id FROM system_jumps"
        ).fetchall():
            graph.setdefault(from_id, set()).add(to_id)
            graph.setdefault(to_id, set()).add(from_id)
        return graph

    def system_region_map(self) -> dict[int, int]:
        """`system_id -> region_id` for every known solar system."""
        rows = self._con.execute("SELECT system_id, region_id FROM solar_systems").fetchall()
        return dict(rows)

    def station_locations(self) -> list[tuple[int, int]]:
        """`(location_id, system_id)` for every known NPC station."""
        return self._con.execute(
            "SELECT location_id, system_id FROM locations WHERE kind = 'station' "
            "AND system_id IS NOT NULL"
        ).fetchall()

    def all_stations(self) -> list[tuple[int, str]]:
        """`(location_id, name)` for every known NPC station, name-sorted -- backs the
        web dashboard's station picker dropdowns."""
        return self._con.execute(
            "SELECT location_id, name FROM locations WHERE kind = 'station' ORDER BY name"
        ).fetchall()

    def region_names(self) -> dict[int, str]:
        """`region_id -> name` for every region in `locations`."""
        return dict(
            self._con.execute(
                "SELECT location_id, name FROM locations WHERE kind = 'region'"
            ).fetchall()
        )

    # -- Locations (stations + regions) ------------------------------------

    def pending_station_ids(self) -> list[int]:
        """Distinct NPC-station `location_id`s in `orders_latest` not yet in `locations`."""
        rows = self._con.execute(
            """
            SELECT DISTINCT o.location_id
            FROM orders_latest o
            LEFT JOIN locations l ON l.location_id = o.location_id
            WHERE l.location_id IS NULL AND o.location_id < ?
            """,
            [MAX_NPC_STATION_ID],
        ).fetchall()
        return [r[0] for r in rows]

    def pending_region_ids(self) -> list[int]:
        """Distinct `region_id`s in `snapshots` not yet in `locations`."""
        rows = self._con.execute(
            """
            SELECT DISTINCT s.region_id
            FROM snapshots s
            LEFT JOIN locations l ON l.location_id = s.region_id
            WHERE l.location_id IS NULL
            """
        ).fetchall()
        return [r[0] for r in rows]

    def write_location(
        self, location_id: int, kind: str, name: str, system_id: int | None = None
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO locations VALUES (?, ?, ?, ?)",
            [location_id, kind, name, system_id],
        )

    # -- Market history -----------------------------------------------------

    def count_order_types(self, region_id: int) -> int:
        """Number of distinct `type_id`s currently traded in `region_id` (per `orders_latest`)."""
        row = self._con.execute(
            "SELECT COUNT(DISTINCT type_id) FROM orders_latest WHERE region_id = ?",
            [region_id],
        ).fetchone()
        return row[0]

    def pending_history_types(self, region_id: int, fetch_date: date) -> list[int]:
        """Distinct `type_id`s for `region_id` not yet logged for `fetch_date`."""
        rows = self._con.execute(
            """
            SELECT DISTINCT o.type_id
            FROM orders_latest o
            LEFT JOIN history_fetch_log log
                ON log.region_id = o.region_id
                AND log.type_id = o.type_id
                AND log.fetch_date = ?
            WHERE o.region_id = ? AND log.type_id IS NULL
            """,
            [fetch_date, region_id],
        ).fetchall()
        return [r[0] for r in rows]

    def write_history(
        self, region_id: int, type_id: int, fetch_date: date, rows: list[dict]
    ) -> None:
        """Upsert daily history rows and record the fetch-log marker (even if `rows` is empty).

        Rows go in via a temp Parquet + `read_parquet` rather than one `execute()` per row --
        duckdb's python binding costs ~12ms/row here, and a type has up to ~365 daily rows.
        """
        self._con.execute("BEGIN TRANSACTION")
        try:
            if rows:
                df = pl.DataFrame(
                    {
                        "region_id": [region_id] * len(rows),
                        "type_id": [type_id] * len(rows),
                        "date": [date.fromisoformat(r["date"]) for r in rows],
                        "average": [r["average"] for r in rows],
                        "highest": [r["highest"] for r in rows],
                        "lowest": [r["lowest"] for r in rows],
                        "order_count": [r["order_count"] for r in rows],
                        "volume": [r["volume"] for r in rows],
                    },
                    schema_overrides={
                        "region_id": pl.Int64,
                        "type_id": pl.Int64,
                        "order_count": pl.Int64,
                        "volume": pl.Int64,
                    },
                )
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_path = Path(tmp_dir) / "history.parquet"
                    df.write_parquet(tmp_path)
                    self._con.execute(
                        "INSERT OR REPLACE INTO history SELECT * FROM read_parquet(?)",
                        [str(tmp_path)],
                    )
            self._con.execute(
                "INSERT OR REPLACE INTO history_fetch_log VALUES (?, ?, ?, ?)",
                [region_id, type_id, fetch_date, _to_naive_utc(datetime.now(UTC))],
            )
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")

    # -- Snapshot diff / fill model --------------------------------------------

    def write_diff_results(self, events: list[dict], metrics: list[dict]) -> None:
        """Replace diff events/metrics for whichever `(from_snapshot_id, to_snapshot_id)`
        pairs appear in `metrics` (idempotent -- safe to recompute the same pair).

        Writes via temp Parquet + `read_parquet`, not `executemany` -- duckdb's python
        binding turns out to be slow per row in this environment even for all-scalar
        columns (~12ms/row measured, not just the LIST-column case found in Faza 3),
        which is impractical at the event counts a real region's diff produces.
        """
        pairs = {(m["from_snapshot_id"], m["to_snapshot_id"]) for m in metrics}
        self._con.execute("BEGIN TRANSACTION")
        try:
            for from_id, to_id in pairs:
                self._con.execute(
                    "DELETE FROM snapshot_diff_events "
                    "WHERE from_snapshot_id = ? AND to_snapshot_id = ?",
                    [from_id, to_id],
                )
                self._con.execute(
                    "DELETE FROM book_diff_metrics "
                    "WHERE from_snapshot_id = ? AND to_snapshot_id = ?",
                    [from_id, to_id],
                )
            with tempfile.TemporaryDirectory() as tmp_dir:
                if events:
                    events_df = pl.DataFrame(
                        {
                            "region_id": [e["region_id"] for e in events],
                            "location_id": [e["location_id"] for e in events],
                            "type_id": [e["type_id"] for e in events],
                            "order_id": [e["order_id"] for e in events],
                            "is_buy_order": [e["is_buy_order"] for e in events],
                            "event_type": [e["event_type"] for e in events],
                            "price": [e["price"] for e in events],
                            "filled_volume": [e["filled_volume"] for e in events],
                            "from_snapshot_id": [e["from_snapshot_id"] for e in events],
                            "to_snapshot_id": [e["to_snapshot_id"] for e in events],
                            "from_fetched_at": [
                                _to_naive_utc(e["from_fetched_at"]) for e in events
                            ],
                            "to_fetched_at": [_to_naive_utc(e["to_fetched_at"]) for e in events],
                        },
                        schema_overrides={"filled_volume": pl.Int64},
                    )
                    events_path = Path(tmp_dir) / "events.parquet"
                    events_df.write_parquet(events_path)
                    self._con.execute(
                        "INSERT INTO snapshot_diff_events SELECT * FROM read_parquet(?)",
                        [str(events_path)],
                    )
                if metrics:
                    metrics_df = pl.DataFrame(
                        {
                            "region_id": [m["region_id"] for m in metrics],
                            "location_id": [m["location_id"] for m in metrics],
                            "type_id": [m["type_id"] for m in metrics],
                            "from_snapshot_id": [m["from_snapshot_id"] for m in metrics],
                            "to_snapshot_id": [m["to_snapshot_id"] for m in metrics],
                            "from_fetched_at": [
                                _to_naive_utc(m["from_fetched_at"]) for m in metrics
                            ],
                            "to_fetched_at": [_to_naive_utc(m["to_fetched_at"]) for m in metrics],
                            "elapsed_hours": [m["elapsed_hours"] for m in metrics],
                            "bid_volume_per_hour": [m["bid_volume_per_hour"] for m in metrics],
                            "ask_volume_per_hour": [m["ask_volume_per_hour"] for m in metrics],
                            "relists_per_hour": [m["relists_per_hour"] for m in metrics],
                            "bid_competitor_count": [m["bid_competitor_count"] for m in metrics],
                            "ask_competitor_count": [m["ask_competitor_count"] for m in metrics],
                            "estimated_bid_capture_rate": [
                                m["estimated_bid_capture_rate"] for m in metrics
                            ],
                            "estimated_ask_capture_rate": [
                                m["estimated_ask_capture_rate"] for m in metrics
                            ],
                        }
                    )
                    metrics_path = Path(tmp_dir) / "metrics.parquet"
                    metrics_df.write_parquet(metrics_path)
                    self._con.execute(
                        "INSERT INTO book_diff_metrics SELECT * FROM read_parquet(?)",
                        [str(metrics_path)],
                    )
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        else:
            self._con.execute("COMMIT")

    def calibration_actual_volume(
        self, region_id: int, type_id: int, target_date: date
    ) -> float | None:
        """The real region-wide traded volume for `type_id` on `target_date`, per history."""
        row = self._con.execute(
            "SELECT volume FROM history WHERE region_id = ? AND type_id = ? AND date = ?",
            [region_id, type_id, target_date],
        ).fetchone()
        return row[0] if row is not None else None

    def calibration_estimated_volume(
        self, region_id: int, type_id: int, target_date: date
    ) -> float:
        """Sum of `filled_volume` from diff events whose interval falls on `target_date`."""
        row = self._con.execute(
            """
            SELECT COALESCE(SUM(filled_volume), 0)
            FROM snapshot_diff_events
            WHERE region_id = ? AND type_id = ? AND event_type = 'fill'
            AND CAST(to_fetched_at AS DATE) = ?
            """,
            [region_id, type_id, target_date],
        ).fetchone()
        return row[0]

    # -- Faza 8: ingest run log ------------------------------------------------

    def write_ingest_run(
        self,
        run_id: str,
        command: str,
        region_id: int,
        started_at: datetime,
        finished_at: datetime,
        success: bool,
        order_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO ingest_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                command,
                region_id,
                _to_naive_utc(started_at),
                _to_naive_utc(finished_at),
                success,
                order_count,
                error_message,
            ],
        )

    def recent_ingest_runs(self, since: datetime, region_id: int | None = None) -> list[tuple]:
        """`(run_id, command, region_id, started_at, finished_at, success, order_count,
        error_message)` for runs finished at or after `since`, newest first."""
        params: list[object] = [_to_naive_utc(since)]
        region_filter = ""
        if region_id is not None:
            region_filter = "AND region_id = ?"
            params.append(region_id)
        return self._con.execute(
            f"""
            SELECT run_id, command, region_id, started_at, finished_at, success,
                   order_count, error_message
            FROM ingest_runs
            WHERE finished_at >= ? {region_filter}
            ORDER BY finished_at DESC
            """,
            params,
        ).fetchall()

    # -- Faza 8: saved plans -----------------------------------------------------

    def write_plan_run(
        self,
        run_id: str,
        run_type: str,
        created_at: datetime,
        from_station_id: int,
        to_station_id: int | None,
        total_expected_profit: float,
        expected_profit_unit: str,
    ) -> None:
        self._con.execute(
            "INSERT INTO plan_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                run_type,
                _to_naive_utc(created_at),
                from_station_id,
                to_station_id,
                total_expected_profit,
                expected_profit_unit,
            ],
        )

    def write_plan_lines(self, run_id: str, lines: list[dict]) -> None:
        for line in lines:
            self._con.execute(
                "INSERT INTO plan_lines VALUES (?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    line["type_id"],
                    line["type_name"],
                    line["suggested_quantity"],
                    line["suggested_buy_price"],
                    line["suggested_sell_price"],
                ],
            )

    def plan_run(self, run_id: str) -> tuple | None:
        """`(run_id, run_type, created_at, from_station_id, to_station_id,
        total_expected_profit, expected_profit_unit)` or None."""
        return self._con.execute(
            "SELECT run_id, run_type, created_at, from_station_id, to_station_id, "
            "total_expected_profit, expected_profit_unit FROM plan_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()

    def list_plan_runs(self, run_type: str | None = None, limit: int = 50) -> list[tuple]:
        """`(run_id, run_type, created_at, from_station_id, to_station_id,
        total_expected_profit, expected_profit_unit)`, newest first -- backs the web
        dashboard's plan-run listing."""
        if run_type is None:
            return self._con.execute(
                "SELECT run_id, run_type, created_at, from_station_id, to_station_id, "
                "total_expected_profit, expected_profit_unit FROM plan_runs "
                "ORDER BY created_at DESC LIMIT ?",
                [limit],
            ).fetchall()
        return self._con.execute(
            "SELECT run_id, run_type, created_at, from_station_id, to_station_id, "
            "total_expected_profit, expected_profit_unit FROM plan_runs "
            "WHERE run_type = ? ORDER BY created_at DESC LIMIT ?",
            [run_type, limit],
        ).fetchall()

    def plan_lines_for_run(self, run_id: str) -> list[tuple]:
        """`(type_id, type_name, suggested_quantity, suggested_buy_price,
        suggested_sell_price)` for one saved plan run."""
        return self._con.execute(
            "SELECT type_id, type_name, suggested_quantity, suggested_buy_price, "
            "suggested_sell_price FROM plan_lines WHERE run_id = ?",
            [run_id],
        ).fetchall()

    # -- Faza 8: trade log --------------------------------------------------------

    def write_trade(
        self,
        trade_id: str,
        run_id: str | None,
        type_id: int,
        logged_at: datetime,
        quantity: float,
        actual_buy_price: float,
        actual_sell_price: float,
        notes: str | None = None,
    ) -> None:
        self._con.execute(
            "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trade_id,
                run_id,
                type_id,
                _to_naive_utc(logged_at),
                quantity,
                actual_buy_price,
                actual_sell_price,
                notes,
            ],
        )

    def trades_for_report(self, since: datetime | None = None) -> list[tuple]:
        """`(trade_id, run_id, type_id, logged_at, quantity, actual_buy_price,
        actual_sell_price, plan_type_name, plan_buy_price, plan_sell_price)` -- left-joined
        against `plan_lines` so trades logged without a referenced plan (or whose plan line
        doesn't match on type_id) still come back, with the plan columns NULL."""
        where = ""
        params: list[object] = []
        if since is not None:
            where = "WHERE t.logged_at >= ?"
            params = [_to_naive_utc(since)]
        return self._con.execute(
            f"""
            SELECT t.trade_id, t.run_id, t.type_id, t.logged_at, t.quantity,
                   t.actual_buy_price, t.actual_sell_price,
                   pl.type_name, pl.suggested_buy_price, pl.suggested_sell_price
            FROM trades t
            LEFT JOIN plan_lines pl ON pl.run_id = t.run_id AND pl.type_id = t.type_id
            {where}
            ORDER BY t.logged_at
            """,
            params,
        ).fetchall()

    def trade_log(self, since: datetime | None = None) -> list[tuple]:
        """`(trade_id, run_id, type_id, type_name, logged_at, quantity, actual_buy_price,
        actual_sell_price, plan_buy_price, plan_sell_price, notes)`, newest first. Like
        `trades_for_report` but also carries notes and falls back to `types` for the name
        when the trade has no matching plan line."""
        where = ""
        params: list[object] = []
        if since is not None:
            where = "WHERE t.logged_at >= ?"
            params = [_to_naive_utc(since)]
        return self._con.execute(
            f"""
            SELECT t.trade_id, t.run_id, t.type_id,
                   COALESCE(pl.type_name, ty.name, CAST(t.type_id AS TEXT)),
                   t.logged_at, t.quantity, t.actual_buy_price, t.actual_sell_price,
                   pl.suggested_buy_price, pl.suggested_sell_price, t.notes
            FROM trades t
            LEFT JOIN plan_lines pl ON pl.run_id = t.run_id AND pl.type_id = t.type_id
            LEFT JOIN types ty ON ty.type_id = t.type_id
            {where}
            ORDER BY t.logged_at DESC
            """,
            params,
        ).fetchall()

    # -- ops telemetry (dashboard Status screen) ---------------------------------

    def write_esi_limit_sample(self, sampled_at: datetime, remain: int, reset_s: float) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO esi_limit_samples VALUES (?, ?, ?)",
            [_to_naive_utc(sampled_at), remain, reset_s],
        )

    def latest_esi_limit_sample(self) -> tuple[datetime, int, float] | None:
        """`(sampled_at, remain, reset_s)` of the newest sample, or None."""
        return self._con.execute(
            "SELECT sampled_at, remain, reset_s FROM esi_limit_samples "
            "ORDER BY sampled_at DESC LIMIT 1"
        ).fetchone()

    def write_disk_sample(self, sampled_at: datetime, used_bytes: int, total_bytes: int) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO disk_samples VALUES (?, ?, ?)",
            [_to_naive_utc(sampled_at), used_bytes, total_bytes],
        )

    def latest_disk_sample_time(self) -> datetime | None:
        row = self._con.execute("SELECT MAX(sampled_at) FROM disk_samples").fetchone()
        return _to_aware_utc(row[0]) if row and row[0] is not None else None

    def disk_samples(self, since: datetime) -> list[tuple[datetime, int, int]]:
        """`(sampled_at, used_bytes, total_bytes)` from `since`, oldest first."""
        return self._con.execute(
            "SELECT sampled_at, used_bytes, total_bytes FROM disk_samples "
            "WHERE sampled_at >= ? ORDER BY sampled_at",
            [_to_naive_utc(since)],
        ).fetchall()

    def prune_telemetry(self, before: datetime) -> None:
        cutoff = _to_naive_utc(before)
        self._con.execute("DELETE FROM esi_limit_samples WHERE sampled_at < ?", [cutoff])
        self._con.execute("DELETE FROM disk_samples WHERE sampled_at < ?", [cutoff])
