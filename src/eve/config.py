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
