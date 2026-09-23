import { useCallback, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { Field, NumInput, Screen } from "../components/ui";
import { fmtInt, fmtIsk, fmtNum, fmtPct } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import type { MarketMakingRow } from "../types";

export function MarketMakingScreen() {
  const rowsQ = useLoad(api.getMarketMaking);
  const settingsQ = useLoad(api.getSettings);
  const rows = useMemo(() => rowsQ.data ?? [], [rowsQ.data]);
  const settings = settingsQ.data;
  const error = rowsQ.error ?? settingsQ.error;
  const [minVolume, setMinVolume] = useState(0);
  const [maxComp, setMaxComp] = useState(Infinity);
  const [shown, setShown] = useState(0);

  const data = useMemo(
    () => rows.filter((r) => r.daily_volume >= minVolume && r.relists_per_hour <= maxComp),
    [rows, minVolume, maxComp],
  );
  const threshold = (settings?.ui.mm_actionable_ppd_per_isk_pct ?? 1.4) / 100;
  const stations = useMemo(() => [...new Set(rows.map((r) => r.station_name))], [rows]);

  const columns = useMemo<ColumnDef<MarketMakingRow>[]>(
    () => [
      { accessorKey: "type_name", header: "Item", meta: { filter: "text", width: 200 } },
      { accessorKey: "station_name", header: "Stanica", meta: { filter: "select", options: stations, width: 110 } },
      {
        accessorKey: "suggested_buy",
        header: "Buy",
        cell: (c) => fmtIsk(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 110 },
      },
      {
        accessorKey: "suggested_sell",
        header: "Sell",
        cell: (c) => fmtIsk(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 110 },
      },
      {
        accessorKey: "net_spread_pct",
        header: "Neto spread",
        cell: (c) => fmtPct(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 100 },
      },
      {
        accessorKey: "expected_fills_per_day",
        header: "Fill / dan",
        cell: (c) => fmtNum(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 100 },
      },
      {
        accessorKey: "relists_per_hour",
        header: "Konkurencija / h",
        cell: (c) => fmtNum(c.getValue<number>(), 2),
        meta: { numeric: true, filter: "max", width: 120 },
      },
      {
        accessorKey: "tied_capital",
        header: "Vezani kapital",
        cell: (c) => fmtNum(c.getValue<number>()),
        meta: { numeric: true, filter: "max", width: 120 },
      },
      {
        accessorKey: "profit_per_day",
        header: "Profit / dan",
        cell: (c) => fmtNum(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 120 },
      },
      {
        accessorKey: "profit_per_day_per_isk",
        header: "Profit/dan po ISK",
        cell: (c) => fmtPct(c.getValue<number>() * 100, 2),
        meta: { numeric: true, filter: "min", width: 140, key: true },
      },
    ],
    [stations],
  );

  const toolbar = (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
      <Field label="Min. dnevni volumen regiona (kom)">
        <NumInput value={minVolume} onChange={(n) => setMinVolume(Number.isFinite(n) ? n : 0)} width={110} min={0} />
      </Field>
      <Field label="Max konkurencija (promena vrha / h)">
        <NumInput
          value={Number.isFinite(maxComp) ? maxComp : NaN}
          onChange={(n) => setMaxComp(Number.isFinite(n) ? n : Infinity)}
          width={110}
          min={0}
          step={0.5}
          aria-label="Max konkurencija"
        />
      </Field>
      <p className="ml-auto pb-1 text-dim">
        Sortirano po profitu/dan po ISK kapitala. Označeno: ≥ {fmtNum(threshold * 100)}%.
      </p>
    </div>
  );

  const onRows = useCallback((r: MarketMakingRow[]) => setShown(r.length), []);
  const footer = error ? (
    <span role="alert" className="font-mono text-[12px] text-sodium">
      {"✕"} {error}
    </span>
  ) : (
    <span className="font-mono text-[12px] text-dim">
      {rowsQ.data ? `${fmtInt(shown)} / ${fmtInt(rows.length)} redova` : "Učitavanje…"}
    </span>
  );

  return (
    <Screen toolbar={toolbar} footer={footer}>
      <DataTable
        data={data}
        columns={columns}
        initialSort={[{ id: "profit_per_day_per_isk", desc: true }]}
        rowMark={(r) => (r.profit_per_day_per_isk >= threshold ? "good" : undefined)}
        getRowId={(r) => `${r.location_id}-${r.type_id}`}
        onRowsChange={onRows}
        empty={
          rowsQ.data
            ? "Nema kandidata. Treba bar 2 snapshota (eve features + eve diff) i history za stanice iz station_pairs."
            : "Učitavanje…"
        }
      />
    </Screen>
  );
}
