"""Request-scoped dependencies. `get_store` opens/closes a fresh `DuckDBStore` per request
(never one connection for the server's whole lifetime) -- DuckDB only allows one process to
hold a database file open for read-write at a time, and the cron ingest job
(`scripts/cron_ingest.sh`) opens its own short-lived connection every 5-15 minutes. A
persistent server-lifetime connection would permanently lock cron out; this mirrors the
CLI's own per-invocation `with DuckDBStore(...) as store:` pattern instead.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends

from eve.config import Settings
from eve.store.duckdb_store import DuckDBStore


def get_settings() -> Settings:
    return Settings()


def get_store(settings: Settings = Depends(get_settings)) -> Iterator[DuckDBStore]:
    with DuckDBStore(settings.storage.duckdb_path) as store:
        yield store
