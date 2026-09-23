"""Trade-log form and the expected-vs-actual report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse

from eve.ops.trades import log_trade, report_by_item, report_by_run
from eve.store.duckdb_store import DuckDBStore
from eve.web.deps import get_store
from eve.web.templating import templates

router = APIRouter()


@router.get("/trades")
def trade_form(request: Request, run_id: str | None = Query(None)):
    return templates.TemplateResponse(request, "trade_form.html", {"run_id": run_id})


@router.post("/trades")
def trade_submit(
    type_id: int = Form(...),
    quantity: float = Form(...),
    buy_price: float = Form(...),
    sell_price: float = Form(...),
    run_id: str | None = Form(None),
    notes: str | None = Form(None),
    store: DuckDBStore = Depends(get_store),
):
    log_trade(
        store,
        type_id=type_id,
        quantity=quantity,
        buy_price=buy_price,
        sell_price=sell_price,
        run_id=run_id or None,
        notes=notes or None,
    )
    return RedirectResponse(url="/report", status_code=303)


@router.get("/report")
def report_page(
    request: Request, days: int | None = Query(None), store: DuckDBStore = Depends(get_store)
):
    since = datetime.now(UTC) - timedelta(days=days) if days is not None else None
    by_item = report_by_item(store, since)
    by_run = report_by_run(store, since)
    return templates.TemplateResponse(
        request, "report.html", {"by_item": by_item, "by_run": by_run, "days": days}
    )
