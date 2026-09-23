"""FastAPI app factory for the local web dashboard (`eve serve`)."""

from __future__ import annotations

import duckdb
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from eve.web.routes import book, dashboard, plans, trades
from eve.web.templating import STATIC_DIR, templates


def create_app() -> FastAPI:
    app = FastAPI(title="EVE Market Engine")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(dashboard.router)
    app.include_router(plans.router)
    app.include_router(trades.router)
    app.include_router(book.router)

    @app.exception_handler(duckdb.IOException)
    async def duckdb_busy_handler(request: Request, exc: duckdb.IOException):
        return templates.TemplateResponse(request, "errors/busy.html", {}, status_code=503)

    return app
