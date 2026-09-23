// Shapes mirror the duckdb tables in src/eve/store/duckdb_store.py.
// Timestamps are ISO-8601 UTC strings; ids are numbers (BIGINT fits in a JS double for EVE ids).

/** snapshots */
export interface Snapshot {
  snapshot_id: string;
  region_id: number;
  fetched_at: string;
  expires_at: string;
  order_count: number;
}

/** health.RegionHealth: latest snapshot per region + staleness */
export interface RegionHealth {
  region_id: number;
  region_name: string;
  last_snapshot: Snapshot | null;
  age_minutes: number | null;
  stale: boolean;
}

/** ingest_runs (failed rows only in the errors list) */
export interface IngestRun {
  run_id: string;
  command: string;
  region_id: number;
  started_at: string;
  finished_at: string;
  success: boolean;
  order_count: number | null;
  error_message: string | null;
}

/** NEW table esi_limit_samples (not persisted yet; backend work) */
export interface EsiLimitSample {
  sampled_at: string;
  remain: number;
  reset_s: number;
}

/** NEW table disk_samples (not persisted yet; backend work) */
export interface DiskSample {
  sampled_at: string;
  used_bytes: number;
  total_bytes: number;
}

export interface StatusReport {
  now: string;
  stale_after_minutes: number;
  regions: RegionHealth[];
  /** null until the backend samples the ESI error limit */
  esi_limit: EsiLimitSample | null;
  esi_limit_max: number;
  disk: DiskSample[];
  disk_warn_pct: number;
  runs_24h_ok: number;
  runs_24h_failed: number;
  history_last_ok_at: string | null;
  recent_errors: IngestRun[];
}

/** locations (NPC stations only in v1) */
export interface Station {
  location_id: number;
  name: string;
  region_id: number;
}

export type ExitMode = "instant" | "listing";
export type ExitModeChoice = ExitMode | "both";

export interface HaulRequest {
  from_station_id: number;
  to_station_id: number;
  budget_isk: number;
  cargo_m3: number;
  max_cargo_value_isk: number;
  exit_mode: ExitModeChoice;
  hold_days: number;
}

/** One shopping-list line (solver ShoppingListLine). */
export interface HaulLine {
  type_id: number;
  type_name: string;
  quantity: number;
  unit_buy_price: number;
  /** "mixed" = the line is split across both exit modes */
  exit_mode: ExitMode | "mixed";
  target_sell_price: number;
  /** net of fees */
  profit: number;
  /** net profit / investment, in percent */
  roi_pct: number;
  margin_pct: number;
  /** destination market activity; null without history */
  daily_volume: number | null;
  daily_volume_isk: number | null;
  share_of_daily_volume_pct: number | null;
}

export interface HaulResult {
  /** saved plan_runs id: reference it when logging trades */
  run_id: string;
  request: HaulRequest;
  lines: HaulLine[];
  total_investment: number;
  cargo_m3_used: number;
  net_profit: number;
  jumps: number;
  profit_per_hour: number;
}

/** book_features + book_diff_metrics joined per (location, type) */
export interface MarketMakingRow {
  type_id: number;
  type_name: string;
  location_id: number;
  station_name: string;
  suggested_buy: number;
  suggested_sell: number;
  net_spread_pct: number;
  expected_fills_per_day: number;
  /** book_diff_metrics.relists_per_hour: top-of-book changes per hour */
  relists_per_hour: number;
  tied_capital: number;
  profit_per_day: number;
  /** history volume, items/day; used by the min-volume filter */
  daily_volume: number;
  /** profit per day per ISK of capital; default sort key */
  profit_per_day_per_isk: number;
}

/** trades joined to plan_lines via run_id */
export interface Trade {
  trade_id: string;
  run_id: string | null;
  type_id: number;
  type_name: string;
  logged_at: string;
  quantity: number;
  actual_buy_price: number;
  actual_sell_price: number;
  /** from plan_lines; null when the trade has no run_id */
  expected_profit: number | null;
  actual_profit: number;
  notes: string | null;
}

export interface Settings {
  skills: Record<string, number>;
  standings: { faction: number; corporation: number };
  station_pairs: { from_station_id: number; to_station_id: number }[];
  filters: { min_price: number; max_price: number };
  hauling: {
    max_cargo_value_isk: number;
    listing_daily_volume_share: number;
    default_hold_days: number;
    time_per_jump_minutes: number;
  };
  market_making: {
    min_near_top_orders: number;
    max_capital_share_per_item: number;
  };
  budget: { default_isk: number };
  cargo: { default_m3: number };
  health: { stale_after_minutes: number; error_lookback_hours: number };
  /** frontend-only thresholds for the "actionable" row marker */
  ui: { haul_actionable_roi_pct: number; mm_actionable_ppd_per_isk_pct: number };
}
