"""FastAPI app factory for the local web dashboard (`eve serve`)."""

from __future__ import annotations

from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from eve.web.routes import api, book, dashboard, plans, trades
from eve.web.templating import STATIC_DIR, templates

FRONTEND_DIST = Path(__file__).parent / "frontend_dist"


def create_app() -> FastAPI:
    app = FastAPI(title="EVE Market Engine")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(dashboard.router)
    app.include_router(plans.router)
    app.include_router(trades.router)
    app.include_router(book.router)
    app.include_router(api.router)

    # React dashboard (frontend/, `npm run build`). Client-side routes fall back to index.html.
    if (FRONTEND_DIST / "index.html").is_file():
        app.mount("/app/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="app-assets")

        @app.get("/app", include_in_schema=False)
        @app.get("/app/{path:path}", include_in_schema=False)
        def spa(path: str = ""):
            if "." in path.rsplit("/", 1)[-1]:
                raise HTTPException(status_code=404)
            return FileResponse(FRONTEND_DIST / "index.html")

    @app.exception_handler(duckdb.IOException)
    async def duckdb_busy_handler(request: Request, exc: duckdb.IOException):
        return templates.TemplateResponse(request, "errors/busy.html", {}, status_code=503)

    return app
