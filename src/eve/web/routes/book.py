"""Item search + per-item order-book view (best bid/ask, near-top depth, history)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from eve.store.duckdb_store import DuckDBStore
from eve.web.deps import get_store
from eve.web.templating import templates

router = APIRouter()


@router.get("/book")
def book_search(
    request: Request,
    station_id: int | None = Query(None),
    q: str | None = Query(None),
    store: DuckDBStore = Depends(get_store),
):
    results = store.search_types_by_name(q) if q else []
    return templates.TemplateResponse(
        request, "book_search.html", {"station_id": station_id, "q": q, "results": results}
    )


@router.get("/book/{station_id}/{type_id}")
def book_detail(
    request: Request, station_id: int, type_id: int, store: DuckDBStore = Depends(get_store)
):
    station_system = dict(store.station_locations())
    system_id = station_system.get(station_id)
    if system_id is None:
        raise HTTPException(status_code=404, detail=f"unknown station {station_id}")
    region_id = store.system_region_map().get(system_id)
    if region_id is None:
        raise HTTPException(status_code=404, detail=f"no region known for station {station_id}")

    row = store.latest_book_features_for_type(region_id, station_id, type_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail="no book_features snapshot for this item/station yet"
        )

    names = store.types_by_id([type_id])
    type_name = names.get(type_id, (str(type_id), 0.0))[0]

    return templates.TemplateResponse(
        request,
        "book_detail.html",
        {"station_id": station_id, "type_id": type_id, "type_name": type_name, "row": row},
    )
