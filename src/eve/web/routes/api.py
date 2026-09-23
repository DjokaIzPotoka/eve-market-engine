"""JSON API for the React dashboard (`frontend/`). Thin over `ops/*` and the solvers, exactly
like the HTML routes: no business logic here, and a fresh `DuckDBStore` per request via
`get_store` (see `web/deps.py`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from eve.config import Settings
from eve.hauling.optimizer import solve_hauling
from eve.ops.config_writer import apply_settings_update
from eve.ops.health import check_health
from eve.ops.market_making_view import market_making_rows
from eve.ops.plans import save_hauling_plan
from eve.ops.trades import trade_log_rows
from eve.store.duckdb_store import DuckDBStore
from eve.web.api_schemas import (
    ESI_ERROR_LIMIT_MAX,
    DiskSampleOut,
    EsiLimitOut,
    HaulLineOut,
    HaulRequestIn,
    HaulResultOut,
    IngestRunOut,
    MarketMakingRowOut,
    RegionHealthOut,
    SettingsOut,
    SnapshotOut,
    StationOut,
    StatusOut,
    TradeOut,
    iso,
)
from eve.web.deps import get_config_path, get_settings, get_store

router = APIRouter(prefix="/api")

DISK_WARN_PCT = 85.0
DISK_CHART_DAYS = 14


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


@router.get("/status", response_model=StatusOut)
def status(
    store: DuckDBStore = Depends(get_store), settings: Settings = Depends(get_settings)
) -> StatusOut:
    now = datetime.now(UTC)
    report = check_health(store, settings)
    names = store.region_names()

    regions = []
    for r in report.regions:
        meta = store.latest_snapshot_meta(r.region_id)
        regions.append(
            RegionHealthOut(
                region_id=r.region_id,
                region_name=names.get(r.region_id, str(r.region_id)),
                last_snapshot=SnapshotOut(
                    snapshot_id=meta.snapshot_id,
                    region_id=meta.region_id,
                    fetched_at=iso(meta.fetched_at),
                    expires_at=iso(meta.expires_at),
                    order_count=meta.order_count,
                )
                if meta
                else None,
                age_minutes=r.age_minutes,
                stale=r.stale,
            )
        )

    lookback = timedelta(hours=settings.health.error_lookback_hours)
    runs = store.recent_ingest_runs(now - max(lookback, timedelta(hours=24)))
    day_start = _naive(now - timedelta(hours=24))
    day_runs = [row for row in runs if row[4] >= day_start]
    error_start = _naive(now - lookback)
    errors = [
        IngestRunOut(
            run_id=row[0],
            command=row[1],
            region_id=row[2],
            started_at=iso(row[3]),
            finished_at=iso(row[4]),
            success=row[5],
            order_count=row[6],
            error_message=row[7],
        )
        for row in runs
        if not row[5] and row[4] >= error_start
    ]
    history_ok = [row[4] for row in runs if row[1] == "ingest-history" and row[5]]

    esi_sample = store.latest_esi_limit_sample()
    disk = store.disk_samples(now - timedelta(days=DISK_CHART_DAYS))

    return StatusOut(
        now=iso(now),
        stale_after_minutes=settings.health.stale_after_minutes,
        regions=regions,
        esi_limit=EsiLimitOut(
            sampled_at=iso(esi_sample[0]), remain=esi_sample[1], reset_s=esi_sample[2]
        )
        if esi_sample
        else None,
        esi_limit_max=ESI_ERROR_LIMIT_MAX,
        disk=[
            DiskSampleOut(sampled_at=iso(t), used_bytes=used, total_bytes=total)
            for t, used, total in disk
        ],
        disk_warn_pct=DISK_WARN_PCT,
        runs_24h_ok=sum(1 for row in day_runs if row[5]),
        runs_24h_failed=sum(1 for row in day_runs if not row[5]),
        history_last_ok_at=iso(max(history_ok)) if history_ok else None,
        recent_errors=errors,
    )


@router.get("/stations", response_model=list[StationOut])
def stations(store: DuckDBStore = Depends(get_store)) -> list[StationOut]:
    system_of = dict(store.station_locations())
    region_of = store.system_region_map()
    return [
        StationOut(
            location_id=location_id,
            name=name,
            region_id=region_of.get(system_of.get(location_id, -1), 0),
        )
        for location_id, name in store.all_stations()
    ]


@router.post("/haul", response_model=HaulResultOut)
def haul(
    req: HaulRequestIn,
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> HaulResultOut:
    # The cargo-value cap is a per-request override of the configured default.
    effective = settings.model_copy(
        update={
            "hauling": settings.hauling.model_copy(
                update={"max_cargo_value_isk": req.max_cargo_value_isk}
            )
        }
    )
    try:
        plan = solve_hauling(
            store,
            req.from_station_id,
            req.to_station_id,
            req.budget_isk,
            req.cargo_m3,
            effective,
            req.exit_mode,
            req.hold_days,
        )
    except AssertionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_id = save_hauling_plan(store, plan)
    return HaulResultOut(
        run_id=run_id,
        request=req,
        lines=[
            HaulLineOut(
                type_id=line.type_id,
                type_name=line.type_name,
                quantity=line.quantity,
                unit_buy_price=line.max_buy_price,
                exit_mode=line.exit_mode,
                target_sell_price=line.target_sell_price,
                profit=line.net_profit,
                roi_pct=line.roi_pct,
                margin_pct=line.margin_pct,
                daily_volume=line.daily_volume,
                daily_volume_isk=line.daily_volume_isk,
                share_of_daily_volume_pct=line.share_of_daily_volume_pct,
            )
            for line in plan.lines
        ],
        total_investment=plan.total_investment,
        cargo_m3_used=plan.cargo_m3_used,
        net_profit=plan.net_profit,
        jumps=plan.jumps,
        profit_per_hour=plan.profit_per_hour,
    )


@router.get("/market-making", response_model=list[MarketMakingRowOut])
def market_making(
    station: int | None = Query(None, description="One station; default: every pair end"),
    capital: float | None = Query(None, gt=0),
    store: DuckDBStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> list[MarketMakingRowOut]:
    if station is not None:
        station_ids = [station]
    else:
        station_ids = sorted(
            {
                sid
                for pair in settings.station_pairs
                for sid in (pair.from_station_id, pair.to_station_id)
            }
        )
    rows = market_making_rows(store, station_ids, capital or settings.budget.default_isk, settings)
    return [MarketMakingRowOut(**vars(r)) for r in rows]


@router.get("/trades", response_model=list[TradeOut])
def trades(store: DuckDBStore = Depends(get_store)) -> list[TradeOut]:
    return [TradeOut(**{**vars(t), "logged_at": iso(t.logged_at)}) for t in trade_log_rows(store)]


def _settings_out(settings: Settings) -> SettingsOut:
    return SettingsOut(
        skills=settings.skills,
        standings=settings.standings,
        station_pairs=settings.station_pairs,
        filters=settings.filters,
        hauling=settings.hauling.model_dump(),
        market_making=settings.market_making.model_dump(),
        budget=settings.budget,
        cargo=settings.cargo,
        health=settings.health.model_dump(),
        ui=settings.ui,
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(settings: Settings = Depends(get_settings)) -> SettingsOut:
    return _settings_out(settings)


@router.put("/settings", response_model=SettingsOut)
def write_settings(body: SettingsOut, config_path: Path = Depends(get_config_path)) -> SettingsOut:
    try:
        saved = apply_settings_update(config_path, body.model_dump())
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": ["body", *e["loc"]], "msg": e["msg"]} for e in exc.errors()],
        ) from exc
    return _settings_out(saved)
