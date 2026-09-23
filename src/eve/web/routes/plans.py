"""Saved plan-run listing/detail, and GET+POST forms that trigger `solve_market_making`/
`solve_hauling` (the "run mm/haul from the browser" scope item). No solver logic lives
here -- routes just call the same functions `cli.py` calls and reuse `ops.plans` to save.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from eve.config import Settings
from eve.hauling.optimizer import rank_station_pairs, solve_hauling
from eve.market_making.optimizer import solve_market_making
from eve.ops.plans import save_hauling_plan, save_market_making_plan
from eve.store.duckdb_store import DuckDBStore
from eve.web.deps import get_settings, get_store
from eve.web.templating import templates

router = APIRouter()


@router.get("/plans")
def list_plans(
    request: Request,
    run_type: str | None = Query(None),
    store: DuckDBStore = Depends(get_store),
):
    runs = store.list_plan_runs(run_type)
    return templates.TemplateResponse(
        request, "plans_list.html", {"runs": runs, "run_type": run_type}
    )


@router.get("/plans/{run_id}")
def plan_detail(request: Request, run_id: str, store: DuckDBStore = Depends(get_store)):
    run = store.plan_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no saved plan run {run_id!r}")
    lines = store.plan_lines_for_run(run_id)
    return templates.TemplateResponse(request, "plan_detail.html", {"run": run, "lines": lines})


@router.get("/mm")
def mm_form(
    request: Request,
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    default_station = settings.station_pairs[0].from_station_id if settings.station_pairs else None
    return templates.TemplateResponse(
        request,
        "mm_form.html",
        {
            "station_id": default_station,
            "capital": settings.budget.default_isk,
            "stations": store.all_stations(),
        },
    )


@router.post("/mm")
def mm_submit(
    request: Request,
    station_id: int = Form(...),
    capital: float = Form(...),
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    try:
        plan = solve_market_making(store, station_id, capital, settings)
    except AssertionError as exc:
        return templates.TemplateResponse(
            request,
            "mm_form.html",
            {
                "station_id": station_id,
                "capital": capital,
                "stations": store.all_stations(),
                "error": str(exc),
            },
            status_code=400,
        )
    run_id = save_market_making_plan(store, plan, settings)
    return templates.TemplateResponse(request, "mm_result.html", {"plan": plan, "run_id": run_id})


@router.get("/haul")
def haul_form(
    request: Request,
    from_: int | None = Query(None, alias="from"),
    to: int | None = Query(None),
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    default_pair = settings.station_pairs[0] if settings.station_pairs else None
    return templates.TemplateResponse(
        request,
        "haul_form.html",
        {
            "from_station_id": from_ or (default_pair.from_station_id if default_pair else None),
            "to_station_id": to or (default_pair.to_station_id if default_pair else None),
            "budget": settings.budget.default_isk,
            "cargo_m3": settings.cargo.default_m3,
            "station_pairs": settings.station_pairs,
            "stations": store.all_stations(),
        },
    )


@router.post("/haul")
def haul_submit(
    request: Request,
    from_station_id: int = Form(...),
    to_station_id: int = Form(...),
    budget: float = Form(...),
    cargo_m3: float = Form(...),
    all_pairs: bool = Form(False),
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    try:
        if all_pairs:
            plans = rank_station_pairs(store, settings.station_pairs, budget, cargo_m3, settings)
            run_ids = [save_hauling_plan(store, plan) for plan in plans]
            return templates.TemplateResponse(
                request, "haul_result.html", {"plans": list(zip(plans, run_ids, strict=True))}
            )
        plan = solve_hauling(store, from_station_id, to_station_id, budget, cargo_m3, settings)
    except AssertionError as exc:
        return templates.TemplateResponse(
            request,
            "haul_form.html",
            {
                "from_station_id": from_station_id,
                "to_station_id": to_station_id,
                "budget": budget,
                "cargo_m3": cargo_m3,
                "station_pairs": settings.station_pairs,
                "stations": store.all_stations(),
                "error": str(exc),
            },
            status_code=400,
        )
    run_id = save_hauling_plan(store, plan)
    return templates.TemplateResponse(request, "haul_result.html", {"plans": [(plan, run_id)]})
