import { useCallback, useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { Field, NumInput, Screen, SegBar } from "../components/ui";
import { clampPct, fmtIsk, fmtNum, fmtPct, fmtSigned } from "../lib/format";
import type { ExitModeChoice, HaulLine, HaulRequest, HaulResult, Settings, Station } from "../types";

type Row = HaulLine & { total: number };

const nullable = (v: number | null, f: (n: number) => string) =>
  v == null ? <span className="text-dim">n/a</span> : f(v);

const toRow = (l: HaulLine): Row => ({ ...l, total: l.quantity * l.unit_buy_price });

export function HaulingScreen() {
  const [stations, setStations] = useState<Station[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [req, setReq] = useState<HaulRequest | null>(null);
  const [result, setResult] = useState<HaulResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);
  const [visible, setVisible] = useState<Row[]>([]);

  useEffect(() => {
    Promise.all([api.getStations(), api.getSettings()])
      .then(([st, s]) => {
      setStations(st);
      setSettings(s);
      const pair = s.station_pairs[0];
      setReq({
        from_station_id: pair?.from_station_id ?? st[0]?.location_id ?? 0,
        to_station_id: pair?.to_station_id ?? st[1]?.location_id ?? 0,
        budget_isk: s.budget.default_isk,
        cargo_m3: s.cargo.default_m3,
        max_cargo_value_isk: s.hauling.max_cargo_value_isk,
        exit_mode: "both",
        hold_days: s.hauling.default_hold_days,
      });
    })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const run = useCallback(async () => {
    if (!req) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.runHaul(req));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [req]);

  const rows = useMemo(() => (result ? result.lines.filter((l) => l.quantity > 0).map(toRow) : []), [result]);
  const roiThreshold = settings?.ui.haul_actionable_roi_pct ?? 8;

  const columns = useMemo<ColumnDef<Row>[]>(
    () => [
      { accessorKey: "type_name", header: "Item", meta: { filter: "text", width: 230 } },
      {
        accessorKey: "quantity",
        header: "Količina",
        cell: (c) => fmtNum(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 100 },
      },
      {
        accessorKey: "unit_buy_price",
        header: "Kupovna / kom",
        cell: (c) => fmtIsk(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 130 },
      },
      {
        accessorKey: "total",
        header: "Ukupno",
        cell: (c) => fmtNum(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 130 },
      },
      {
        accessorKey: "exit_mode",
        header: "Izlaz",
        meta: { filter: "select", options: ["instant", "listing", "mixed"], width: 90 },
      },
      {
        accessorKey: "target_sell_price",
        header: "Ciljna prodajna / kom",
        cell: (c) => fmtIsk(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 150 },
      },
      {
        accessorKey: "profit",
        header: "Profit",
        cell: (c) => fmtSigned(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 120, key: true },
      },
      {
        accessorKey: "roi_pct",
        header: "ROI",
        cell: (c) => fmtPct(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 80 },
      },
      {
        accessorKey: "daily_volume",
        header: "Dnevni vol. (kom)",
        cell: (c) => nullable(c.getValue<number | null>(), (v) => fmtNum(v)),
        sortUndefined: "last",
        meta: { numeric: true, filter: "min", width: 130 },
      },
      {
        accessorKey: "share_of_daily_volume_pct",
        header: "Naš udeo",
        cell: (c) => nullable(c.getValue<number | null>(), (v) => fmtPct(v)),
        sortUndefined: "last",
        meta: { numeric: true, filter: "max", width: 100 },
      },
    ],
    [],
  );

  // Totals are over the full plan, not the filtered view: they describe the trip.
  const invested = result?.total_investment ?? 0;
  const cargoUsed = result?.cargo_m3_used ?? 0;
  const profit = result?.net_profit ?? 0;
  const budget = result?.request.budget_isk ?? req?.budget_isk ?? 0;
  const cap = result?.request.cargo_m3 ?? req?.cargo_m3 ?? 0;
  const investPct = budget > 0 ? (invested / budget) * 100 : 0;
  const cargoPct = cap > 0 ? (cargoUsed / cap) * 100 : 0;
  // A plan over a limit is a solver bug (CLAUDE.md), never shown as a bar past 100%.
  const violation = result && (invested > budget + 0.5 || cargoUsed > cap + 1e-6);

  async function copyMultibuy() {
    const src = visible.length ? visible : rows;
    const text = src.map((r) => `${r.type_name}\t${r.quantity}`).join("\n");
    await navigator.clipboard.writeText(text);
    setCopied(src.length);
    setTimeout(() => setCopied(null), 2000);
  }

  const set = <K extends keyof HaulRequest>(k: K, v: HaulRequest[K]) => setReq((r) => (r ? { ...r, [k]: v } : r));

  const toolbar = req && (
    <form
      className="flex flex-wrap items-end gap-x-4 gap-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
    >
      <Field label="Od">
        <select
          className="field w-[190px]"
          value={req.from_station_id}
          onChange={(e) => set("from_station_id", Number(e.target.value))}
        >
          {stations.map((s) => (
            <option key={s.location_id} value={s.location_id}>
              {s.name.split(" - ")[0]}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Do">
        <select
          className="field w-[190px]"
          value={req.to_station_id}
          onChange={(e) => set("to_station_id", Number(e.target.value))}
        >
          {stations.map((s) => (
            <option key={s.location_id} value={s.location_id}>
              {s.name.split(" - ")[0]}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Budžet (ISK)">
        <NumInput value={req.budget_isk} onChange={(n) => set("budget_isk", n)} width={130} min={0} step={1_000_000} />
      </Field>
      <Field label="Cargo (m³)">
        <NumInput value={req.cargo_m3} onChange={(n) => set("cargo_m3", n)} width={90} min={0} step={100} />
      </Field>
      <Field label="Max vrednost tereta (ISK)" hint="0 = bez limita">
        <NumInput
          value={req.max_cargo_value_isk}
          onChange={(n) => set("max_cargo_value_isk", n)}
          width={130}
          min={0}
          step={1_000_000}
        />
      </Field>
      <Field label="Izlazni režim">
        <select
          className="field w-[110px]"
          value={req.exit_mode}
          onChange={(e) => set("exit_mode", e.target.value as ExitModeChoice)}
        >
          <option value="instant">instant</option>
          <option value="listing">listing</option>
          <option value="both">oba</option>
        </select>
      </Field>
      <Field label="Držanje (dana)">
        <select
          className="field w-[70px]"
          value={req.hold_days}
          onChange={(e) => set("hold_days", Number(e.target.value))}
        >
          {[...new Set([1, 3, 7, req.hold_days])]
            .sort((a, b) => a - b)
            .map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
        </select>
      </Field>
      <button type="submit" className="btn btn-primary mb-px" disabled={busy}>
        {busy ? "Računam…" : "Izračunaj"}
      </button>
    </form>
  );

  const footer = (
    <div className="flex flex-col gap-2">
      {error && (
        <div role="alert" className="border border-sodium bg-sodium/15 px-3 py-1 font-mono text-sodium">
          {"✕"} {error}
        </div>
      )}
      {violation && (
        <div role="alert" className="border border-sodium bg-sodium/15 px-3 py-1 font-mono text-sodium">
          {"✕"} GREŠKA SOLVERA: plan prelazi budžet ili cargo. Ne koristi ovu listu.
        </div>
      )}
    <div className="flex items-center gap-6">
      <div className="grid flex-1 grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5">
        <span className="text-dim">Investicija</span>
        <SegBar pct={investPct} label="Investicija u odnosu na budžet" />
        <span className={`font-mono ${clampPct(investPct) >= 90 ? "text-sodium" : ""}`}>
          {fmtNum(invested)} / {fmtNum(budget)}
          {"  "}
          {fmtNum(clampPct(investPct), 0)}%{clampPct(investPct) >= 90 && " ▲ blizu ivice"}
        </span>
        <span className="text-dim">Cargo</span>
        <SegBar pct={cargoPct} label="Cargo u odnosu na kapacitet" />
        <span className={`font-mono ${clampPct(cargoPct) >= 90 ? "text-sodium" : ""}`}>
          {fmtNum(cargoUsed, 1)} / {fmtNum(cap)} m³
          {"  "}
          {fmtNum(clampPct(cargoPct), 0)}%{clampPct(cargoPct) >= 90 && " ▲ blizu ivice"}
        </span>
      </div>
      <dl className="shrink-0 border-l border-bulkhead pl-6 font-mono">
        <div className="flex justify-between gap-6">
          <dt className="font-sans text-dim">Profit</dt>
          <dd className="font-semibold text-telemetry">{fmtSigned(profit)}</dd>
        </div>
        <div className="flex justify-between gap-6">
          <dt className="font-sans text-dim">Profit / h</dt>
          <dd>{result ? fmtSigned(result.profit_per_hour) : "-"}</dd>
        </div>
      </dl>
      <div className="flex shrink-0 flex-col items-stretch gap-1">
        <button type="button" className="btn" onClick={copyMultibuy} disabled={rows.length === 0}>
          Kopiraj za multibuy
        </button>
        <span aria-live="polite" className="h-4 text-center text-[11px] text-telemetry">
          {copied != null ? `kopirano ${copied} linija` : ""}
        </span>
      </div>
    </div>
    </div>
  );

  const onRows = useCallback((r: Row[]) => setVisible(r), []);

  return (
    <Screen toolbar={toolbar} footer={footer}>
      {result ? (
        <DataTable
          data={rows}
          columns={columns}
          initialSort={[{ id: "profit", desc: true }]}
          rowMark={(r) => (r.roi_pct >= roiThreshold ? "good" : undefined)}
          getRowId={(r) => String(r.type_id)}
          onRowsChange={onRows}
          empty="Nema redova za zadate filtere."
        />
      ) : (
        <p className="p-4 text-dim">Podesi parametre i pokreni Izračunaj.</p>
      )}
    </Screen>
  );
}
