"""Shared Jinja2 environment -- one instance imported by every route module."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def num(value: float | int | None, decimals: int = 0) -> str:
    """Human-readable number: thousands separators, fixed decimals ("n/a" for None)."""
    if value is None:
        return "n/a"
    return f"{value:,.{decimals}f}"


def station_label(station_id: int | None, names: dict[int, str] | None) -> str:
    """Station name for display; falls back to the raw ID if the name isn't known."""
    if station_id is None:
        return ""
    return (names or {}).get(station_id) or str(station_id)


templates.env.filters["num"] = num
templates.env.filters["station"] = station_label
