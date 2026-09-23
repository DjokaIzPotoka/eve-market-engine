"""GET / -- health overview. The foundational monitoring page the other pages link back to."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from eve.config import Settings
from eve.ops.health import check_health
from eve.store.duckdb_store import DuckDBStore
from eve.web.deps import get_settings, get_store
from eve.web.templating import templates

router = APIRouter()


@router.get("/")
def dashboard_home(
    request: Request,
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    report = check_health(store, settings)
    return templates.TemplateResponse(request, "index.html", {"report": report})
