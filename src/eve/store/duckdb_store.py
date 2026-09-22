"""DuckDB-backed storage for order snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
