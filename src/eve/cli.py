"""EVE Market Engine CLI."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import typer
import uvicorn

from eve.config import Settings
from eve.features.book_features import compute_book_features
from eve.fill.fill_model import run_diff_for_region
from eve.hauling.optimizer import EXIT_MODES, HaulingPlan, rank_station_pairs, solve_hauling
from eve.ingest.esi_client import EsiClient
from eve.ingest.locations import resolve_locations
from eve.ingest.sde import check_and_load_sde
from eve.market_making.optimizer import MarketMakingPlan, solve_market_making
from eve.ops.health import HealthReport, check_health
from eve.ops.ingest_runner import (
    run_and_record_history,
    run_and_record_ingest,
    run_ingest_all,
    run_ingest_history_all,
)
from eve.ops.plans import save_hauling_plan, save_market_making_plan
from eve.ops.trades import log_trade, report_by_item, report_by_run
from eve.store.duckdb_store import DuckDBStore
from eve.web.app import create_app

app = typer.Typer(help="EVE Market Engine - market making advisor & hauling optimizer.")


@app.callback()
def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@app.command()
def ingest(region: int = typer.Option(..., help="Region ID to ingest orders for.")) -> None:
    """Fetch and store a market orders snapshot for a region."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        outcome = asyncio.run(run_and_record_ingest(store, region, settings))
    if not outcome.success:
        typer.echo(f"ingest failed for region {region}: {outcome.error_message}")
        raise typer.Exit(code=1)
    status = "cached, fetch skipped" if outcome.skipped else "ingested"
    typer.echo(f"region {region}: {outcome.order_count} orders, {status} (run {outcome.run_id})")


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
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        outcome = asyncio.run(run_and_record_history(store, region, settings))
    typer.echo(
        f"history for region {region}: {outcome.order_count} fetched -- "
        f"{outcome.error_message or 'no errors'} (run {outcome.run_id})"
    )


@app.command(name="ingest-all")
def ingest_all_cmd() -> None:
    """Ingest orders for every region touched by the configured station pairs."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        outcomes = asyncio.run(run_ingest_all(store, settings))
    for outcome in outcomes:
        status = "ok" if outcome.success else f"FAILED: {outcome.error_message}"
        typer.echo(f"region {outcome.region_id}: {outcome.order_count} orders -- {status}")
    if any(not o.success for o in outcomes):
        raise typer.Exit(code=1)


@app.command(name="ingest-history-all")
def ingest_history_all_cmd() -> None:
    """Ingest daily history for every region touched by the configured station pairs."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        outcomes = asyncio.run(run_ingest_history_all(store, settings))
    for outcome in outcomes:
        status = "ok" if outcome.success else f"FAILED: {outcome.error_message}"
        typer.echo(f"region {outcome.region_id}: {outcome.order_count} fetched -- {status}")
    if any(not o.success for o in outcomes):
        raise typer.Exit(code=1)


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


def _echo_health_report(report: HealthReport) -> None:
    for region in report.regions:
        if region.last_snapshot_id is None:
            typer.echo(f"region {region.region_id}: NO SNAPSHOT")
            continue
        flag = "STALE" if region.stale else "ok"
        typer.echo(
            f"region {region.region_id}: {flag}, last snapshot {region.age_minutes:.1f} min ago, "
            f"{region.order_count} orders"
        )
    for error in report.recent_errors:
        typer.echo(f"error: {error.command} region {error.region_id}: {error.error_message}")
    typer.echo("healthy" if report.healthy else "UNHEALTHY")


@app.command()
def health() -> None:
    """Report last snapshot age/order count per region, and recent ingest errors."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        health_report = check_health(store, settings)
    _echo_health_report(health_report)
    if not health_report.healthy:
        raise typer.Exit(code=1)


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
        run_id = save_market_making_plan(store, plan, settings)
    _echo_market_making_plan(plan)
    typer.echo(f"saved as run {run_id} -- reference it with `eve log-trade --run-id {run_id}`")


def _fmt_volume(daily_volume: float | None) -> str:
    return f"{daily_volume:,.0f}/day" if daily_volume is not None else "n/a"


def _echo_hauling_plan(plan: HaulingPlan) -> None:
    typer.echo(f"{plan.from_station_id} -> {plan.to_station_id}: {len(plan.lines)} item(s)")
    for line in plan.lines:
        typer.echo(
            f"  {line.type_name}: {line.quantity:.0f} @ up to {line.max_buy_price:.2f} ISK "
            f"-> {line.exit_mode} @ {line.target_sell_price:.2f} ISK "
            f"(daily volume {_fmt_volume(line.daily_volume)}, "
            f"ROI {line.roi_pct:,.1f}%, margin {line.margin_pct:,.1f}%)"
        )
    typer.echo(
        f"  investment {plan.total_investment:,.0f} ISK, cargo {plan.cargo_m3_used:,.1f} m3, "
        f"net profit {plan.net_profit:,.0f} ISK (ROI {plan.roi_pct:,.1f}%, "
        f"margin {plan.margin_pct:,.1f}%), {plan.jumps} jumps, "
        f"{plan.profit_per_hour:,.0f} ISK/hour"
    )
    hold = f", hold {plan.hold_days} d" if plan.exit_mode != "instant" else ""
    typer.echo(
        f"  mode {plan.exit_mode}{hold}: {plan.profit_per_day_incl_hold:,.0f} ISK/day incl. holding"
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
    exit_mode: str = typer.Option(
        "both",
        "--exit-mode",
        help="instant (sell into buy orders), listing (own sell order), or both.",
    ),
    hold_days: int = typer.Option(
        None, "--hold-days", help="Days to hold listings (1/3/7...); default from config."
    ),
) -> None:
    """Compute the optimal hauling shopping list between two stations."""
    if exit_mode not in EXIT_MODES:
        raise typer.BadParameter(f"--exit-mode must be one of {', '.join(EXIT_MODES)}")
    if hold_days is not None and hold_days < 1:
        raise typer.BadParameter("--hold-days must be at least 1")
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        if all_pairs:
            plans = rank_station_pairs(
                store, settings.station_pairs, budget, cargo, settings, exit_mode, hold_days
            )
            for rank, plan in enumerate(plans, start=1):
                typer.echo(f"#{rank}")
                _echo_hauling_plan(plan)
                run_id = save_hauling_plan(store, plan)
                typer.echo(f"saved as run {run_id}")
        else:
            if from_ is None or to is None:
                raise typer.BadParameter("--from and --to are required unless --all-pairs is set")
            plan = solve_hauling(store, from_, to, budget, cargo, settings, exit_mode, hold_days)
            _echo_hauling_plan(plan)
            run_id = save_hauling_plan(store, plan)
            typer.echo(
                f"saved as run {run_id} -- reference it with `eve log-trade --run-id {run_id}`"
            )


@app.command(name="log-trade")
def log_trade_cmd(
    type_id: int = typer.Option(..., help="Item type ID traded."),
    quantity: float = typer.Option(..., help="Quantity actually traded."),
    buy_price: float = typer.Option(..., help="Actual buy price per unit, in ISK."),
    sell_price: float = typer.Option(..., help="Actual sell price per unit, in ISK."),
    run_id: str = typer.Option(
        None, help="Saved plan run this trade is based on (printed by `mm`/`haul`)."
    ),
    notes: str = typer.Option(None, help="Free-text notes."),
) -> None:
    """Record a real trade, optionally against a saved mm/haul plan run."""
    settings = Settings()
    with DuckDBStore(settings.storage.duckdb_path) as store:
        trade_id = log_trade(
            store,
            type_id=type_id,
            quantity=quantity,
            buy_price=buy_price,
            sell_price=sell_price,
            run_id=run_id,
            notes=notes,
        )
    typer.echo(f"logged trade {trade_id}")


@app.command()
def report(
    days: int = typer.Option(
        None, help="Only include trades logged in the last N days. Omit for all trades."
    ),
) -> None:
    """Expected vs. actual profit from logged trades, per item and per plan run."""
    settings = Settings()
    since = datetime.now(UTC) - timedelta(days=days) if days is not None else None
    with DuckDBStore(settings.storage.duckdb_path) as store:
        by_item = report_by_item(store, since)
        by_run = report_by_run(store, since)

    typer.echo("by item:")
    for row in by_item:
        name = row["type_name"] or str(row["type_id"])
        expected = f"{row['expected_profit']:,.2f}" if row["expected_profit"] else "n/a"
        typer.echo(
            f"  {name}: expected {expected} ISK, actual {row['actual_profit']:,.2f} ISK "
            f"({row['trade_count']} trade(s))"
        )

    typer.echo("by run:")
    for row in by_run:
        run_label = row["run_id"] or "(no run)"
        expected = f"{row['expected_profit']:,.2f}" if row["expected_profit"] else "n/a"
        typer.echo(
            f"  {run_label}: expected {expected} ISK, actual {row['actual_profit']:,.2f} ISK "
            f"({row['trade_count']} trade(s))"
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Host/interface to bind the web dashboard to."),
    port: int = typer.Option(8000, help="Port to bind the web dashboard to."),
) -> None:
    """Launch the local web dashboard (health, plan triggers, trade log, book view)."""
    uvicorn.run(create_app(), host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
