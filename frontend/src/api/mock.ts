import type { EveApi } from "./index";
import type {
  DiskSample,
  HaulLine,
  HaulRequest,
  HaulResult,
  IngestRun,
  MarketMakingRow,
  Settings,
  StatusReport,
  Station,
  Trade,
} from "../types";

// Deterministic PRNG so the mock looks the same on every reload.
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

const STATIONS: Station[] = [
  { location_id: 60003760, name: "Jita IV - Moon 4 - Caldari Navy Assembly Plant", region_id: 10000002 },
  { location_id: 60008494, name: "Amarr VIII (Oris) - Emperor Family Academy", region_id: 10000043 },
];

// type_id, name, base price, unit m3
const ITEMS: [number, string, number, number][] = [
  [34, "Tritanium", 5.1, 0.01],
  [35, "Pyerite", 9.8, 0.01],
  [36, "Mexallon", 78, 0.01],
  [37, "Isogen", 135, 0.01],
  [38, "Nocxium", 780, 0.01],
  [39, "Zydrine", 1650, 0.01],
  [40, "Megacyte", 2100, 0.01],
  [11399, "Morphite", 14200, 0.01],
  [16274, "Helium Isotopes", 380, 0.03],
  [17888, "Nitrogen Isotopes", 420, 0.03],
  [3689, "Mechanical Parts", 4300, 0.5],
  [9848, "Robotics", 98000, 1],
  [2489, "Hobgoblin II", 26500, 5],
  [2454, "Hammerhead II", 34000, 5],
  [12058, "Damage Control II", 640000, 5],
  [2048, "Damage Control I", 21000, 5],
  [2281, "Adaptive Invulnerability Field II", 410000, 5],
  [3841, "Large Shield Extender II", 195000, 20],
  [5973, "Warp Scrambler II", 1180000, 5],
  [447, "Stasis Webifier II", 940000, 5],
  [11563, "Micro Warpdrive II", 76000, 10],
  [12084, "Sensor Booster II", 730000, 5],
  [3244, "Neutron Blaster Cannon II", 890000, 10],
  [2929, "Inertial Stabilizers II", 210000, 5],
  [16273, "Liquid Ozone", 260, 0.4],
  [16272, "Heavy Water", 105, 0.4],
  [44992, "PLEX", 3400000, 0.01],
  [21815, "Nanite Repair Paste", 18000, 0.01],
  [29668, "Multispectrum Energized Membrane II", 1250000, 5],
  [4310, "Nanofiber Internal Structure II", 1290000, 5],
  [11578, "Overdrive Injector System II", 1050000, 5],
  [1306, "Shield Booster II", 158000, 5],
  [3576, "Medium Shield Extender II", 60000, 10],
  [2410, "Warrior II", 47000, 5],
  [15510, "Republic Fleet Antimatter", 3900, 0.01],
  [193, "Antimatter Charge S", 82, 0.0025],
];

const NOW = Date.parse("2026-09-23T14:32:07Z");
const iso = (ms: number) => new Date(ms).toISOString().replace(/\.\d+Z$/, "Z");

function status(): StatusReport {
  const r = rng(7);
  const disk: DiskSample[] = [];
  const total = 500e9;
  const n = 14 * 4;
  for (let i = 0; i < n; i++) {
    // Steady growth, one retention purge partway through.
    const used = 130e9 + i * 1.6e9 + r() * 3e9 - (i > 40 ? 12e9 : 0);
    disk.push({ sampled_at: iso(NOW - (n - i) * 6 * 3600e3), used_bytes: used, total_bytes: total });
  }
  const errors: IngestRun[] = [
    {
      run_id: "r-9914",
      command: "ingest-all",
      region_id: 10000002,
      started_at: iso(NOW - 92 * 60e3),
      finished_at: iso(NOW - 91 * 60e3),
      success: false,
      order_count: null,
      error_message: "ESI 504 Gateway Timeout on /markets/10000002/orders/ page 231",
    },
    {
      run_id: "r-9871",
      command: "ingest-all",
      region_id: 10000043,
      started_at: iso(NOW - 165 * 60e3),
      finished_at: iso(NOW - 164 * 60e3),
      success: false,
      order_count: null,
      error_message: "httpx.ReadTimeout after 30s",
    },
  ];
  return {
    now: iso(NOW),
    stale_after_minutes: 20,
    regions: [
      {
        region_id: 10000002,
        region_name: "The Forge",
        age_minutes: 4.2,
        stale: false,
        last_snapshot: {
          snapshot_id: "s-20260923-1427",
          region_id: 10000002,
          fetched_at: iso(NOW - 252e3),
          expires_at: iso(NOW + 48e3),
          order_count: 412883,
        },
      },
      {
        region_id: 10000043,
        region_name: "Domain",
        age_minutes: 4.7,
        stale: false,
        last_snapshot: {
          snapshot_id: "s-20260923-1427b",
          region_id: 10000043,
          fetched_at: iso(NOW - 280e3),
          expires_at: iso(NOW + 20e3),
          order_count: 98140,
        },
      },
    ],
    esi_limit: { sampled_at: iso(NOW - 15e3), remain: 97, reset_s: 42 },
    esi_limit_max: 100,
    disk,
    disk_warn_pct: 85,
    runs_24h_ok: 287,
    runs_24h_failed: errors.length,
    history_last_ok_at: "2026-09-23T06:10:44Z",
    recent_errors: errors,
  };
}

function haul(req: HaulRequest): HaulResult {
  const r = rng(req.from_station_id + Math.round(req.budget_isk / 1000));
  const vol = new Map<number, number>();
  const candidates: HaulLine[] = ITEMS.map(([id, name, price, unitVol]) => {
    vol.set(id, unitVol);
    const buy = price * (0.97 + r() * 0.06);
    const margin = 0.02 + r() * 0.22;
    const mode: HaulLine["exit_mode"] =
      req.exit_mode === "both" ? (r() > 0.45 ? "instant" : "listing") : req.exit_mode;
    const target = buy * (1 + margin) * (mode === "instant" ? 0.985 : 1.01);
    const holdPenalty = mode === "listing" ? 1 - 0.01 * req.hold_days : 1;
    // No single line may take more than ~9% of the hold, so the mock plan has many lines.
    const qty = Math.max(
      1,
      Math.min(Math.round((req.budget_isk / 12 / buy) * (0.3 + r())), Math.floor((req.cargo_m3 * 0.09) / unitVol)),
    );
    const profit = qty * (target * holdPenalty - buy) - qty * target * 0.0338;
    const dailyVolume = Math.round(qty * (1 + r() * 30));
    return {
      type_id: id,
      type_name: name,
      quantity: qty,
      unit_buy_price: buy,
      exit_mode: mode,
      target_sell_price: target,
      profit,
      roi_pct: (profit / (qty * buy)) * 100,
      margin_pct: (profit / (qty * target)) * 100,
      daily_volume: dailyVolume,
      daily_volume_isk: dailyVolume * target,
      share_of_daily_volume_pct: (qty / dailyVolume) * 100,
    };
  })
    .filter((l) => l.profit > 0)
    .sort((a, b) => b.roi_pct - a.roi_pct);

  // Greedy fill to budget, cargo and cargo value (mock only; the real solver is the MILP).
  const lines: HaulLine[] = [];
  let spent = 0;
  let cargo = 0;
  for (const l of candidates) {
    const unitVol = vol.get(l.type_id) ?? 1;
    const maxByBudget = Math.floor((req.budget_isk - spent) / l.unit_buy_price);
    const maxByCargo = Math.floor((req.cargo_m3 - cargo) / unitVol);
    const maxByValue =
      req.max_cargo_value_isk > 0
        ? Math.floor((req.max_cargo_value_isk - spent) / l.unit_buy_price)
        : Infinity;
    const q = Math.min(l.quantity, maxByBudget, maxByCargo, maxByValue);
    if (q < 1) continue;
    const k = q / l.quantity;
    lines.push({
      ...l,
      quantity: q,
      profit: l.profit * k,
      share_of_daily_volume_pct: l.daily_volume ? (q / l.daily_volume) * 100 : null,
    });
    spent += q * l.unit_buy_price;
    cargo += q * unitVol;
  }
  const profit = lines.reduce((a, l) => a + l.profit, 0);
  return {
    run_id: "mock-run",
    request: req,
    lines,
    total_investment: spent,
    cargo_m3_used: cargo,
    net_profit: profit,
    jumps: 14,
    profit_per_hour: profit / ((14 * 3 + 6) / 60),
  };
}

function marketMaking(): MarketMakingRow[] {
  const r = rng(42);
  const rows: MarketMakingRow[] = [];
  for (let n = 0; n < 320; n++) {
    const [id, name, price] = ITEMS[n % ITEMS.length];
    const st = STATIONS[n % 2];
    const wave = Math.floor(n / ITEMS.length);
    const bid = price * (0.9 + r() * 0.15);
    const spread = 0.8 + r() * 14;
    const ask = bid * (1 + (spread + 4.5) / 100);
    const fills = 0.2 + r() * 40;
    const capital = bid * fills * (0.5 + r() * 2);
    const profitDay = capital * (spread / 100) * (0.4 + r() * 0.9) * 0.1;
    rows.push({
      type_id: id + wave * 100000,
      type_name: wave ? `${name} (${wave + 1})` : name,
      location_id: st.location_id,
      station_name: st.name.split(" - ")[0],
      suggested_buy: bid + 0.01,
      suggested_sell: ask - 0.01,
      net_spread_pct: spread,
      expected_fills_per_day: fills,
      relists_per_hour: r() * r() * 30,
      tied_capital: capital,
      profit_per_day: profitDay,
      daily_volume: Math.round(fills * (5 + r() * 60)),
      profit_per_day_per_isk: profitDay / capital,
    });
  }
  return rows.sort((a, b) => b.profit_per_day_per_isk - a.profit_per_day_per_isk);
}

function trades(): Trade[] {
  const r = rng(99);
  const out: Trade[] = [];
  for (let n = 0; n < 48; n++) {
    const [id, name, price] = ITEMS[Math.floor(r() * ITEMS.length)];
    const qty = Math.max(1, Math.round(2e7 / price / (1 + r() * 6)));
    const buy = price * (0.97 + r() * 0.04);
    const planned = 0.05 + r() * 0.14;
    const ratio = r() < 0.3 ? 0.3 + r() * 0.45 : 0.8 + r() * 0.35;
    const expected = qty * buy * planned;
    const hasPlan = r() > 0.1;
    const actual = expected * ratio;
    out.push({
      trade_id: `t-${1000 + n}`,
      run_id: hasPlan ? `run-${10 + Math.floor(n / 4)}` : null,
      type_id: id,
      type_name: name,
      logged_at: iso(NOW - n * 9.5 * 3600e3),
      quantity: qty,
      actual_buy_price: buy,
      actual_sell_price: buy + actual / qty,
      expected_profit: hasPlan ? expected : null,
      actual_profit: actual,
      notes: r() > 0.85 ? "slow fill, relisted twice" : null,
    });
  }
  return out;
}

let settings: Settings = {
  skills: {
    broker_relations: 4,
    accounting: 4,
    margin_trading: 3,
    advanced_broker_relations: 3,
    trade: 5,
    retail: 4,
    wholesale: 2,
    tycoon: 0,
  },
  standings: { faction: 2.1, corporation: 5.0 },
  station_pairs: [{ from_station_id: 60003760, to_station_id: 60008494 }],
  filters: { min_price: 0, max_price: 0 },
  hauling: {
    max_cargo_value_isk: 0,
    listing_daily_volume_share: 0.1,
    default_hold_days: 1,
    time_per_jump_minutes: 3,
  },
  market_making: { min_near_top_orders: 2, max_capital_share_per_item: 0.1 },
  budget: { default_isk: 500_000_000 },
  cargo: { default_m3: 5000 },
  health: { stale_after_minutes: 20, error_lookback_hours: 24 },
  ui: { haul_actionable_roi_pct: 8, mm_actionable_ppd_per_isk_pct: 1.4 },
};

export const mockApi: EveApi = {
  getStatus: () => delay(status()),
  getStations: () => delay(STATIONS, 30),
  runHaul: (req) => delay(haul(req), 300),
  getMarketMaking: () => delay(marketMaking()),
  getTrades: () => delay(trades()),
  getSettings: () => delay(settings, 40),
  saveSettings: (s) => {
    settings = s;
    return delay(settings, 150);
  },
};
