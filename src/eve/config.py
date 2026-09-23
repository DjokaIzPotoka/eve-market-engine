"""Application configuration, loaded from config/settings.toml."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.toml"

SkillLevel = Annotated[int, Field(ge=0, le=5)]
Standing = Annotated[float, Field(ge=-10.0, le=10.0)]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]


class Skills(BaseModel):
    broker_relations: SkillLevel = 0
    accounting: SkillLevel = 0
    margin_trading: SkillLevel = 0
    advanced_broker_relations: SkillLevel = 0
    trade: SkillLevel = 0
    retail: SkillLevel = 0
    wholesale: SkillLevel = 0
    tycoon: SkillLevel = 0


class Standings(BaseModel):
    faction: Standing = 0.0
    corporation: Standing = 0.0


class Budget(BaseModel):
    default_isk: Positive


class Cargo(BaseModel):
    default_m3: Positive


class StationPair(BaseModel):
    from_station_id: int
    to_station_id: int


class Hauling(BaseModel):
    """Hauling optimizer (Module B) tuning. All fee rates still come from `Fees` --
    these are hauling-specific modeling knobs (MILP sizing, ranking, exit-mode economics).
    """

    # Items whose cheapest source sell price exceeds this get integer MILP variables;
    # cheaper items stay continuous (LP) for solver speed.
    integer_price_threshold_isk: Positive = 1_000_000.0
    time_per_jump_minutes: Positive = 3.0
    listing_daily_volume_share: Annotated[float, Field(gt=0, le=1)] = 0.1
    max_cargo_value_isk: NonNegative = 0.0  # 0 = no limit
    # Listing exit mode: price haircut per day the goods are held, approximating the time
    # value of tied-up capital (1 day = this %, 3 days = 3x, ...).
    listing_discount_pct_per_day: Annotated[float, Field(ge=0, lt=100)] = 1.0
    # Extra haircut for price risk: factor * volatility_pct * sqrt(hold_days), where
    # volatility_pct is the average daily (highest - lowest) / average from ESI history.
    listing_volatility_risk_factor: NonNegative = 0.5
    # Days of destination history averaged for daily volume and volatility.
    history_window_days: Annotated[int, Field(ge=1)] = 14
    default_hold_days: Annotated[int, Field(ge=1)] = 1


class MarketMaking(BaseModel):
    """Market making advisor (Module A) tuning. All fee rates still come from `Fees` --
    these are Module-A-specific modeling knobs (order slots, escrow, sizing, ranking).
    """

    # Order-slot skills (Trade/Retail/Wholesale/Tycoon), base 5 + coefficients per level --
    # see EVE University wiki "Trading" for the current numbers.
    base_order_slots: Annotated[int, Field(ge=1)] = 5
    trade_slots_per_level: Annotated[int, Field(ge=0)] = 4
    retail_slots_per_level: Annotated[int, Field(ge=0)] = 8
    wholesale_slots_per_level: Annotated[int, Field(ge=0)] = 16
    tycoon_slots_per_level: Annotated[int, Field(ge=0)] = 32
    # Margin Trading multiplies buy-order escrow by this factor per skill level (V ~= 23.7%
    # of order value, per EVE University wiki). CCP is phasing this skill out, but it's
    # already modeled in `Skills` since Faza 0, so Module A honors it.
    margin_trading_reduction_factor_per_level: Annotated[float, Field(gt=0, le=1)] = 0.75
    price_tick_isk: Positive = 0.01
    # Order size = expected daily fill * this many days of flow -- sizing beyond the
    # market's daily throughput ties up capital without adding profit/day.
    target_turnover_days: Positive = 1.0
    max_capital_share_per_item: Annotated[float, Field(gt=0, le=1)] = 0.1
    # Both sides of the book must have at least this many orders near the top before a
    # two-sided market-making position is suggested.
    min_near_top_orders: Annotated[int, Field(ge=1)] = 2


class Health(BaseModel):
    """Health-check thresholds for `eve health` (Faza 8 -- ops, not domain fee rates,
    but kept config-driven for the same reason: never hardcode a tunable threshold)."""

    # Ingest cadence is 5-15 min per PLAN.md; a region past this is considered stale.
    stale_after_minutes: Positive = 20.0
    error_lookback_hours: Positive = 24.0


class Filters(BaseModel):
    min_price: NonNegative = 0.0
    max_price: NonNegative = 0.0


class Esi(BaseModel):
    base_url: str = "https://esi.evetech.net/latest"
    contact: str
    app_name: str = "eve-market-engine"
    max_concurrency: Annotated[int, Field(ge=1, le=50)] = 5
    max_retries: Annotated[int, Field(ge=0, le=10)] = 5
    backoff_base_seconds: Positive = 1.0
    error_limit_threshold: Annotated[int, Field(ge=1)] = 10


class Storage(BaseModel):
    raw_data_dir: str = "data/raw"
    duckdb_path: str = "data/eve.duckdb"
    retention_days: Annotated[int, Field(ge=1)] = 30
    sde_dir: str = "data/sde"


class Features(BaseModel):
    depth_levels: Annotated[int, Field(ge=1, le=50)] = 5
    near_top_pct: Annotated[float, Field(gt=0, le=1)] = 0.05


class Fill(BaseModel):
    max_gap_minutes: Annotated[int, Field(ge=1)] = 60
    fill_plausibility_factor: Annotated[float, Field(gt=0)] = 1.0


class Fees(BaseModel):
    """All percentages are plain numbers (3.0 = 3%), except the accounting fraction (a
    per-level multiplier). Defaults match the current (post-2022-rework) CCP formulas --
    see src/eve/fees/fees.py for the formulas themselves."""

    broker_fee_base_pct: Positive = 3.0
    broker_relations_reduction_pct_per_level: NonNegative = 0.3
    faction_standing_reduction_pct_per_point: NonNegative = 0.03
    corp_standing_reduction_pct_per_point: NonNegative = 0.02
    broker_fee_min_pct: Positive = 1.0
    broker_fee_min_isk: Positive = 100.0
    structure_broker_fee_base_pct: NonNegative = 0.5
    # The structure owner's own cut is unknowable without authenticated ESI (out of v1
    # scope per CLAUDE.md) -- this is an assumed default, override per-call if known.
    structure_owner_fee_pct: NonNegative = 0.0
    sales_tax_base_pct: Positive = 7.5
    accounting_reduction_fraction_per_level: Annotated[float, Field(ge=0, le=1)] = 0.11
    relist_discount_base_pct: NonNegative = 50.0
    relist_discount_pct_per_adv_br_level: NonNegative = 6.0
    relist_fee_min_isk: Positive = 100.0


class Settings(BaseSettings):
    """Top-level settings, sourced from `config/settings.toml` (see DEFAULT_CONFIG_PATH).

    Pass `_toml_file=<path>` to load a different file, e.g. in tests.
    """

    model_config = SettingsConfigDict(toml_file=DEFAULT_CONFIG_PATH)

    skills: Skills
    standings: Standings
    budget: Budget
    cargo: Cargo
    station_pairs: list[StationPair]
    filters: Filters
    esi: Esi
    storage: Storage
    features: Features
    fill: Fill
    fees: Fees
    hauling: Hauling
    market_making: MarketMaking
    health: Health

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_file = init_settings.init_kwargs.pop("_toml_file", None) or DEFAULT_CONFIG_PATH
        return (TomlConfigSettingsSource(settings_cls, toml_file=toml_file),)
