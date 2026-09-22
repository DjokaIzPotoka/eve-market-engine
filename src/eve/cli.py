"""EVE Market Engine CLI."""

from __future__ import annotations

import typer

app = typer.Typer(help="EVE Market Engine - market making advisor & hauling optimizer.")


@app.command()
def ingest(region: int = typer.Option(..., help="Region ID to ingest orders for.")) -> None:
    """Fetch and store a market orders snapshot for a region."""
    typer.echo("not implemented yet")


@app.command()
def features() -> None:
    """Compute book features (best bid/ask, depth, order counts) from stored snapshots."""
    typer.echo("not implemented yet")


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
