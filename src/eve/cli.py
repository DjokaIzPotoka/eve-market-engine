"""EVE Market Engine CLI."""

from __future__ import annotations

import asyncio
import logging

import typer

from eve.config import Settings
from eve.features.book_features import compute_book_features
from eve.ingest.esi_client import EsiClient
from eve.ingest.history import ingest_history
from eve.ingest.locations import resolve_locations
from eve.ingest.orders import ingest_region
from eve.ingest.sde import check_and_load_sde
from eve.store.duckdb_store import DuckDBStore

app = typer.Typer(help="EVE Market Engine - market making advisor & hauling optimizer.")


@app.callback()
def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@app.command()
def ingest(region: int = typer.Option(..., help="Region ID to ingest orders for.")) -> None:
    """Fetch and store a market orders snapshot for a region."""
    result = asyncio.run(ingest_region(region, Settings()))
    if result.skipped:
        typer.echo(
            f"snapshot {result.snapshot_id} still fresh (expires {result.expires_at}) - "
            f"{result.order_count} orders, fetch skipped"
        )
    else:
        typer.echo(
            f"ingested snapshot {result.snapshot_id}: {result.order_count} orders "
            f"(expires {result.expires_at})"
        )


@app.command(name="load-sde")
def load_sde() -> None:
    """Load SDE item types (version-checked) and resolve station/region names via ESI."""
    settings = Settings()

    async def run() -> tuple:
        with DuckDBStore(settings.storage.duckdb_path) as store:
            sde_result = await check_and_load_sde(store, settings)
            async with EsiClient(settings.esi) as client:
                loc_result = await resolve_locations(store, client)
        return sde_result, loc_result

    sde_result, loc_result = asyncio.run(run())
    if sde_result.skipped:
        typer.echo(f"SDE already up to date (build {sde_result.build_number})")
    else:
        typer.echo(
            f"loaded {sde_result.types_loaded} types from SDE build {sde_result.build_number}"
        )
    if sde_result.unknown_type_count:
        typer.echo(f"warning: {sde_result.unknown_type_count} type_id(s) have no SDE match")
    typer.echo(f"locations: {loc_result.resolved} resolved, {loc_result.unresolved} unresolved")


@app.command(name="ingest-history")
def ingest_history_cmd(
    region: int = typer.Option(..., help="Region ID to ingest daily history for."),
) -> None:
    """Fetch daily market history for every type currently traded in a region (once per day)."""
    result = asyncio.run(ingest_history(region, Settings()))
    typer.echo(
        f"history for region {result.region_id}: {result.fetched} fetched, "
        f"{result.skipped_already_done} already done today, {result.failed} failed"
    )


@app.command()
def features(
    region: int = typer.Option(..., help="Region ID to compute book features for."),
    station: list[int] = typer.Option(
        None,
        help="Station ID(s) to compute rows for (repeatable). Omit for the whole region "
        "-- slow on a big/liquid region since coverage fans out to every station.",
    ),
) -> None:
    """Compute book features (best bid/ask, depth, order counts) for a region's latest snapshot."""
    settings = Settings()
    location_ids = station or None
    with DuckDBStore(settings.storage.duckdb_path) as store:
        n = compute_book_features(store, region, settings, location_ids=location_ids)
    typer.echo(f"wrote {n} book_features rows for region {region}")


@app.command()
def mm(
    station: int = typer.Option(..., help="Station ID to generate market making suggestions for."),
    capital: float = typer.Option(..., help="Capital available, in ISK."),
) -> None:
    """Suggest market making orders for a station."""
    typer.echo("not implemented yet")


@app.command()
def haul(
    from_: int = typer.Option(..., "--from", help="Origin station ID."),
    to: int = typer.Option(..., help="Destination station ID."),
    budget: float = typer.Option(..., help="Budget available, in ISK."),
    cargo: float = typer.Option(..., help="Cargo capacity, in m3."),
) -> None:
    """Compute the optimal hauling shopping list between two stations."""
    typer.echo("not implemented yet")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
