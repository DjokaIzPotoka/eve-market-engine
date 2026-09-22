"""SDE (Static Data Export) type loading, with build-number version checking."""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from eve.config import Settings
from eve.store.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)

SDE_LATEST_URL = "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
SDE_ZIP_URL = "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip"


@dataclass
class SdeLoadResult:
    build_number: int
    types_loaded: int
    unknown_type_count: int
    skipped: bool


async def fetch_latest_build() -> int:
    """GET latest.jsonl and return the current SDE build number."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(SDE_LATEST_URL)
        response.raise_for_status()
        return int(response.json()["buildNumber"])


async def download_sde_zip(dest_path: Path) -> Path:
    """Stream the full SDE JSONL zip to `dest_path`. Caller is responsible for confirming size."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        async with client.stream("GET", SDE_ZIP_URL) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)
    return dest_path


def extract_types_jsonl(zip_path: Path, dest_dir: Path) -> Path:
    """Extract just `types.jsonl` from the SDE zip (ignores every other member)."""
    with zipfile.ZipFile(zip_path) as zf:
        member = next(
            name for name in zf.namelist() if name == "types.jsonl" or name.endswith("/types.jsonl")
        )
        with zf.open(member) as src, open(dest_dir / "types.jsonl", "wb") as dst:
            dst.write(src.read())
    return dest_dir / "types.jsonl"


async def check_and_load_sde(
    store: DuckDBStore,
    settings: Settings,
    build_number: int | None = None,
    zip_path: Path | None = None,
) -> SdeLoadResult:
    """Load `types` from the SDE, unless the current build is already loaded.

    `build_number` and `zip_path` are injectable seams for tests (and for loading a
    pre-downloaded zip) -- by default the build is fetched from ESI and the zip downloaded.
    """
    if build_number is None:
        build_number = await fetch_latest_build()
    current_build = store.latest_sde_build()
    if current_build == build_number:
        logger.info("SDE already up to date (build %d)", build_number)
        return SdeLoadResult(
            build_number=build_number,
            types_loaded=0,
            unknown_type_count=len(store.unknown_type_ids()),
            skipped=True,
        )

    sde_dir = Path(settings.storage.sde_dir)
    sde_dir.mkdir(parents=True, exist_ok=True)
    owns_zip = zip_path is None
    if zip_path is None:
        zip_path = sde_dir / "eve-online-static-data-latest-jsonl.zip"
        await download_sde_zip(zip_path)
    types_path = extract_types_jsonl(zip_path, sde_dir)
    if owns_zip:
        zip_path.unlink()

    types_loaded = store.load_types_from_jsonl(types_path, build_number)
    unknown = store.unknown_type_ids()
    if unknown:
        logger.warning(
            "%d type_id(s) in orders_latest have no SDE match: %s", len(unknown), unknown
        )
    logger.info("loaded %d types from SDE build %d", types_loaded, build_number)

    return SdeLoadResult(
        build_number=build_number,
        types_loaded=types_loaded,
        unknown_type_count=len(unknown),
        skipped=False,
    )
