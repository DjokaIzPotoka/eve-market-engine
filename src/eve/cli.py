"""EVE Market Engine CLI."""

from __future__ import annotations

import asyncio
import logging

import typer

from eve.config import Settings
from eve.features.book_features import compute_book_features
from eve.fill.fill_model import run_diff_for_region
from eve.hauling.optimizer import HaulingPlan, rank_station_pairs, solve_hauling
from eve.ingest.esi_client import EsiClient
from eve.ingest.history import ingest_history
from eve.ingest.locations import resolve_locations
from eve.ingest.orders import ingest_region
from eve.ingest.sde import check_and_load_sde
from eve.market_making.optimizer import MarketMakingPlan, solve_market_making
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
def diff(region: int = typer.Option(..., help="Region ID to diff retained snapshots for.")) -> None:
    """Diff every consecutive pair of retained raw snapshots into fills/relists/cancels."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        result = run_diff_for_region(store, region, settings)
    typer.echo(
        f"region {result.region_id}: {result.pairs_processed} snapshot pairs diffed, "
        f"{result.pairs_skipped_missing_file} skipped (missing files) -- {result.event_counts}"
    )


def _echo_market_making_plan(plan: MarketMakingPlan) -> None:
    typer.echo(f"station {plan.station_id}: {len(plan.lines)} item(s)")
    for line in plan.lines:
        typer.echo(
            f"  {line.type_name}: buy {line.buy_price:.2f} / sell {line.sell_price:.2f} ISK, "
            f"capital {line.capital_allocated:,.0f} ISK, "
            f"net profit {line.net_profit_per_day:,.2f} ISK/day"
        )
    typer.echo(
        f"  capital used {plan.total_capital_used:,.0f} ISK, {plan.slots_used} order slots, "
        f"total net profit {plan.total_net_profit_per_day:,.2f} ISK/day"
    )


@app.command()
def mm(
    station: int = typer.Option(..., help="Station ID to generate market making suggestions for."),
    capital: float = typer.Option(..., help="Capital available, in ISK."),
) -> None:
    """Suggest market making orders for a station."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        plan = solve_market_making(store, station, capital, settings)
    _echo_market_making_plan(plan)


def _echo_hauling_plan(plan: HaulingPlan) -> None:
    typer.echo(f"{plan.from_station_id} -> {plan.to_station_id}: {len(plan.lines)} item(s)")
    for line in plan.lines:
        typer.echo(
            f"  {line.type_name}: {line.quantity:.0f} @ up to {line.max_buy_price:.2f} ISK "
            f"-> {line.exit_mode} @ {line.target_sell_price:.2f} ISK"
        )
    typer.echo(
        f"  investment {plan.total_investment:,.0f} ISK, cargo {plan.cargo_m3_used:,.1f} m3, "
        f"net profit {plan.net_profit:,.0f} ISK, {plan.jumps} jumps, "
        f"{plan.profit_per_hour:,.0f} ISK/hour"
    )


@app.command()
def haul(
    from_: int = typer.Option(
        None, "--from", help="Origin station ID (required unless --all-pairs)."
    ),
    to: int = typer.Option(None, help="Destination station ID (required unless --all-pairs)."),
    budget: float = typer.Option(..., help="Budget available, in ISK."),
    cargo: float = typer.Option(..., help="Cargo capacity, in m3."),
    all_pairs: bool = typer.Option(
        False, "--all-pairs", help="Solve every configured station pair and rank by profit/hour."
    ),
) -> None:
    """Compute the optimal hauling shopping list between two stations."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        if all_pairs:
            plans = rank_station_pairs(store, settings.station_pairs, budget, cargo, settings)
            for rank, plan in enumerate(plans, start=1):
                typer.echo(f"#{rank}")
                _echo_hauling_plan(plan)
        else:
            if from_ is None or to is None:
                raise typer.BadParameter("--from and --to are required unless --all-pairs is set")
            plan = solve_hauling(store, from_, to, budget, cargo, settings)
            _echo_hauling_plan(plan)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
