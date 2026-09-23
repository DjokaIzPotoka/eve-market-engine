"""JSON shapes for `/api/*`. Field names match `frontend/src/types.ts` one-to-one; keep the
two in sync. Timestamps are ISO-8601 UTC strings ending in `Z`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from eve.config import Budget, Cargo, Filters, Skills, Standings, StationPair, Ui

# ESI error-limit window size (X-ESI-Error-Limit-Remain starts here).
ESI_ERROR_LIMIT_MAX = 100


def iso(dt: datetime) -> str:
    """DuckDB hands back naive UTC datetimes; normalise both kinds to `...Z`."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SnapshotOut(BaseModel):
    snapshot_id: str
    region_id: int
    fetched_at: str
    expires_at: str
    order_count: int


class RegionHealthOut(BaseModel):
    region_id: int
    region_name: str
    last_snapshot: SnapshotOut | None
    age_minutes: float | None
    stale: bool


class IngestRunOut(BaseModel):
    run_id: str
    command: str
    region_id: int
    started_at: str
    finished_at: str
    success: bool
    order_count: int | None
    error_message: str | None


class EsiLimitOut(BaseModel):
    sampled_at: str
    remain: int
    reset_s: float


class DiskSampleOut(BaseModel):
    sampled_at: str
    used_bytes: int
    total_bytes: int


class StatusOut(BaseModel):
    now: str
    stale_after_minutes: float
    regions: list[RegionHealthOut]
    esi_limit: EsiLimitOut | None  # None until telemetry sampling has run
    esi_limit_max: int
    disk: list[DiskSampleOut]
    disk_warn_pct: float
    runs_24h_ok: int
    runs_24h_failed: int
    history_last_ok_at: str | None
    recent_errors: list[IngestRunOut]


class StationOut(BaseModel):
    location_id: int
    name: str
    region_id: int


ExitModeChoice = Literal["instant", "listing", "both"]


class HaulRequestIn(BaseModel):
    from_station_id: int
    to_station_id: int
    budget_isk: float = Field(gt=0)
    cargo_m3: float = Field(gt=0)
    max_cargo_value_isk: float = Field(ge=0, default=0)  # 0 = no limit
    exit_mode: ExitModeChoice = "both"
    hold_days: int = Field(ge=1, default=1)


class HaulLineOut(BaseModel):
    type_id: int
    type_name: str
    quantity: float
    unit_buy_price: float
    exit_mode: Literal["instant", "listing", "mixed"]
    target_sell_price: float
    profit: float
    roi_pct: float
    margin_pct: float
    daily_volume: float | None
    daily_volume_isk: float | None
    share_of_daily_volume_pct: float | None


class HaulResultOut(BaseModel):
    run_id: str
    request: HaulRequestIn
    lines: list[HaulLineOut]
    total_investment: float
    cargo_m3_used: float
    net_profit: float
    jumps: int
    profit_per_hour: float


class MarketMakingRowOut(BaseModel):
    type_id: int
    type_name: str
    location_id: int
    station_name: str
    suggested_buy: float
    suggested_sell: float
    net_spread_pct: float
    expected_fills_per_day: float
    relists_per_hour: float
    tied_capital: float
    profit_per_day: float
    daily_volume: float
    profit_per_day_per_isk: float


class TradeOut(BaseModel):
    trade_id: str
    run_id: str | None
    type_id: int
    type_name: str
    logged_at: str
    quantity: float
    actual_buy_price: float
    actual_sell_price: float
    expected_profit: float | None
    actual_profit: float
    notes: str | None


# -- settings: the subset the dashboard edits (read now, written in the settings phase) ----


class HaulingSettingsOut(BaseModel):
    max_cargo_value_isk: float
    listing_daily_volume_share: float
    default_hold_days: int
    time_per_jump_minutes: float


class MarketMakingSettingsOut(BaseModel):
    min_near_top_orders: int
    max_capital_share_per_item: float


class HealthSettingsOut(BaseModel):
    stale_after_minutes: float
    error_lookback_hours: float


class SettingsOut(BaseModel):
    """Also the PUT body: unknown fields are rejected so a typo cannot silently do nothing."""

    model_config = ConfigDict(extra="forbid")

    skills: Skills
    standings: Standings
    station_pairs: list[StationPair]
    filters: Filters
    hauling: HaulingSettingsOut
    market_making: MarketMakingSettingsOut
    budget: Budget
    cargo: Cargo
    health: HealthSettingsOut
    ui: Ui
