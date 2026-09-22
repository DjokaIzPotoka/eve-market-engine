"""DuckDB-backed storage for order snapshots."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
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

    def sell_orders(self, region_id: int) -> list[tuple[int, int, float, int]]:
        """`(location_id, type_id, price, volume_remain)` for every sell order in the region."""
        return self._con.execute(
            "SELECT location_id, type_id, price, volume_remain FROM orders_latest "
            "WHERE region_id = ? AND is_buy_order = false",
            [region_id],
        ).fetchall()

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
        """Upsert daily history rows and record the fetch-log marker (even if `rows` is empty)."""
        self._con.execute("BEGIN TRANSACTION")
        try:
            for row in rows:
                self._con.execute(
                    "INSERT OR REPLACE INTO history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        region_id,
                        type_id,
                        row["date"],
                        row["average"],
                        row["highest"],
                        row["lowest"],
                        row["order_count"],
                        row["volume"],
                    ],
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
