"""DuckDB-backed storage for order snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb


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
