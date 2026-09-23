import { useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { NumInput, Panel } from "../components/ui";
import type { Settings, Station } from "../types";

const SKILL_LABELS: Record<string, string> = {
  broker_relations: "Broker Relations",
  accounting: "Accounting",
  margin_trading: "Margin Trading",
  advanced_broker_relations: "Advanced Broker Relations",
  trade: "Trade",
  retail: "Retail",
  wholesale: "Wholesale",
  tycoon: "Tycoon",
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-bulkhead/50 py-1 last:border-b-0">
      <span className="text-dim">{label}</span>
      {children}
    </div>
  );
}

export function SettingsScreen() {
  const [s, setS] = useState<Settings | null>(null);
  const [stations, setStations] = useState<Station[]>([]);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
    api.getSettings().then((v) => setS(structuredClone(v)), fail);
    api.getStations().then(setStations, fail);
  }, []);

  if (!s)
    return error ? (
      <p role="alert" className="m-4 border border-sodium bg-sodium/10 px-3 py-2 font-mono text-sodium">
        {"✕"} {error}
      </p>
    ) : (
      <p className="p-4 text-dim">Učitavanje…</p>
    );

  // Immutable-ish updater: clone, mutate the copy, mark dirty.
  const edit = (fn: (draft: Settings) => void) => {
    const d = structuredClone(s);
    fn(d);
    setS(d);
    setDirty(true);
    setState("idle");
  };

  const errors: string[] = [];
  for (const [k, v] of Object.entries(s.skills)) if (!(v >= 0 && v <= 5)) errors.push(`${SKILL_LABELS[k]}: 0–5`);
  if (!(s.standings.faction >= -10 && s.standings.faction <= 10)) errors.push("Faction standing: −10…10");
  if (!(s.standings.corporation >= -10 && s.standings.corporation <= 10)) errors.push("Corp standing: −10…10");
  if (!(s.hauling.listing_daily_volume_share > 0 && s.hauling.listing_daily_volume_share <= 1))
    errors.push("Udeo dnevnog volumena: 0–1");
  if (s.filters.max_price !== 0 && s.filters.max_price < s.filters.min_price) errors.push("Max cena < min cena");
  if (s.station_pairs.some((p) => p.from_station_id === p.to_station_id)) errors.push("Par stanica: Od = Do");

  async function save() {
    if (!s || errors.length) return;
    setState("saving");
    setError(null);
    try {
      setS(structuredClone(await api.saveSettings(s)));
      setDirty(false);
      setState("saved");
    } catch (e) {
      setState("idle");
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const stationName = (id: number) => stations.find((x) => x.location_id === id)?.name.split(" - ")[0] ?? String(id);

  return (
    <form
      className="flex h-full flex-col"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="grid min-h-0 flex-1 grid-cols-2 content-start gap-x-5 gap-y-6 overflow-auto p-4">
        <Panel title="Skillovi">
          {Object.keys(s.skills).map((k) => (
            <Row key={k} label={SKILL_LABELS[k] ?? k}>
              <NumInput
                value={s.skills[k]}
                onChange={(n) => edit((d) => (d.skills[k] = n))}
                width={64}
                min={0}
                max={5}
                aria-label={SKILL_LABELS[k] ?? k}
              />
            </Row>
          ))}
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel title="Standings">
            <Row label="Faction">
              <NumInput value={s.standings.faction} onChange={(n) => edit((d) => (d.standings.faction = n))} width={80} step={0.01} min={-10} max={10} aria-label="Faction standing" />
            </Row>
            <Row label="Corporation">
              <NumInput value={s.standings.corporation} onChange={(n) => edit((d) => (d.standings.corporation = n))} width={80} step={0.01} min={-10} max={10} aria-label="Corporation standing" />
            </Row>
          </Panel>

          <Panel title="Parovi stanica">
            {s.station_pairs.map((p, i) => (
              <div key={i} className="flex items-center gap-2 border-b border-bulkhead/50 py-1.5 last:border-b-0">
                {(["from_station_id", "to_station_id"] as const).map((key) => (
                  <select
                    key={key}
                    aria-label={key === "from_station_id" ? "Od" : "Do"}
                    className="field w-[170px]"
                    value={p[key]}
                    onChange={(e) => edit((d) => (d.station_pairs[i][key] = Number(e.target.value)))}
                  >
                    {stations.map((st) => (
                      <option key={st.location_id} value={st.location_id}>
                        {stationName(st.location_id)}
                      </option>
                    ))}
                  </select>
                ))}
                <button
                  type="button"
                  className="btn ml-auto"
                  onClick={() => edit((d) => d.station_pairs.splice(i, 1))}
                  aria-label={`Ukloni par ${i + 1}`}
                >
                  Ukloni
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn mt-2"
              onClick={() =>
                edit((d) =>
                  d.station_pairs.push({
                    from_station_id: stations[0]?.location_id ?? 0,
                    to_station_id: stations[1]?.location_id ?? 0,
                  }),
                )
              }
            >
              Dodaj par
            </button>
          </Panel>
        </div>

        <Panel title="Filteri likvidnosti i cene">
          <Row label="Min cena (ISK)">
            <NumInput value={s.filters.min_price} onChange={(n) => edit((d) => (d.filters.min_price = n))} width={130} min={0} />
          </Row>
          <Row label="Max cena (ISK, 0 = bez limita)">
            <NumInput value={s.filters.max_price} onChange={(n) => edit((d) => (d.filters.max_price = n))} width={130} min={0} />
          </Row>
          <Row label="Udeo dnevnog volumena za listing (0–1)">
            <NumInput value={s.hauling.listing_daily_volume_share} onChange={(n) => edit((d) => (d.hauling.listing_daily_volume_share = n))} width={80} step={0.01} min={0} max={1} />
          </Row>
          <Row label="Min. ordera blizu vrha (MM)">
            <NumInput value={s.market_making.min_near_top_orders} onChange={(n) => edit((d) => (d.market_making.min_near_top_orders = n))} width={80} min={0} />
          </Row>
          <Row label="Max udeo kapitala po itemu (MM)">
            <NumInput value={s.market_making.max_capital_share_per_item} onChange={(n) => edit((d) => (d.market_making.max_capital_share_per_item = n))} width={80} step={0.01} min={0} max={1} />
          </Row>
        </Panel>

        <Panel title="Pragovi označavanja i zdravlja">
          <Row label="Hauling: akcionabilan kad je ROI ≥ (%)">
            <NumInput value={s.ui.haul_actionable_roi_pct} onChange={(n) => edit((d) => (d.ui.haul_actionable_roi_pct = n))} width={80} step={0.5} min={0} />
          </Row>
          <Row label="Market Making: profit/dan po ISK ≥ (%)">
            <NumInput value={s.ui.mm_actionable_ppd_per_isk_pct} onChange={(n) => edit((d) => (d.ui.mm_actionable_ppd_per_isk_pct = n))} width={80} step={0.5} min={0} />
          </Row>
          <Row label="Snapshot zastareo posle (min)">
            <NumInput value={s.health.stale_after_minutes} onChange={(n) => edit((d) => (d.health.stale_after_minutes = n))} width={80} min={1} />
          </Row>
          <Row label="Podrazumevani budžet (ISK)">
            <NumInput value={s.budget.default_isk} onChange={(n) => edit((d) => (d.budget.default_isk = n))} width={130} min={0} />
          </Row>
          <Row label="Podrazumevani cargo (m³)">
            <NumInput value={s.cargo.default_m3} onChange={(n) => edit((d) => (d.cargo.default_m3 = n))} width={130} min={0} />
          </Row>
        </Panel>
      </div>

      <div className="flex shrink-0 items-center gap-4 border-t border-bulkhead bg-deck px-4 py-2">
        <button type="submit" className="btn btn-primary" disabled={!dirty || errors.length > 0 || state === "saving"}>
          {state === "saving" ? "Upisujem…" : "Sačuvaj u config"}
        </button>
        <span aria-live="polite" className="text-[12px]">
          {errors.length > 0 && <span className="text-sodium">{"✕"} {errors.join(" · ")}</span>}
          {error && <span className="text-sodium">{"✕"} {error}</span>}
          {errors.length === 0 && state === "saved" && <span className="text-telemetry">Sačuvano.</span>}
          {errors.length === 0 && dirty && <span className="text-dim">Nesačuvane izmene.</span>}
        </span>
      </div>
    </form>
  );
}
