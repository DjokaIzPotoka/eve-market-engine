import { useCallback, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "../api";
import { DataTable } from "../components/DataTable";
import { Screen } from "../components/ui";
import { fmtDay, fmtIsk, fmtNum, fmtPct, fmtSigned } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import type { Trade } from "../types";

type Row = Trade & { ratio_pct: number | null };

const UNDERPERFORM = 0.8;

export function TradesScreen() {
  const { data: trades, error } = useLoad(api.getTrades);
  const [shown, setShown] = useState<Row[]>([]);

  const rows = useMemo<Row[]>(
    () =>
      (trades ?? []).map((t) => ({
        ...t,
        ratio_pct: t.expected_profit ? (t.actual_profit / t.expected_profit) * 100 : null,
      })),
    [trades],
  );

  const columns = useMemo<ColumnDef<Row>[]>(
    () => [
      {
        accessorKey: "logged_at",
        header: "Datum",
        cell: (c) => fmtDay(c.getValue<string>()),
        meta: { filter: "text", width: 110 },
      },
      { accessorKey: "type_name", header: "Item", meta: { filter: "text", width: 230 } },
      {
        accessorKey: "quantity",
        header: "Količina",
        cell: (c) => fmtNum(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 100 },
      },
      {
        accessorKey: "actual_buy_price",
        header: "Kupovna",
        cell: (c) => fmtIsk(c.getValue<number>()),
        meta: { numeric: true, width: 130 },
      },
      {
        accessorKey: "actual_sell_price",
        header: "Prodajna",
        cell: (c) => fmtIsk(c.getValue<number>()),
        meta: { numeric: true, width: 130 },
      },
      {
        accessorKey: "expected_profit",
        header: "Očekivan profit",
        cell: (c) => {
          const v = c.getValue<number | null>();
          return v == null ? <span className="text-dim">bez plana</span> : fmtNum(v);
        },
        sortUndefined: "last",
        meta: { numeric: true, filter: "min", width: 140 },
      },
      {
        accessorKey: "actual_profit",
        header: "Ostvaren profit",
        cell: (c) => fmtSigned(c.getValue<number>()),
        meta: { numeric: true, filter: "min", width: 140, key: true },
      },
      {
        accessorKey: "ratio_pct",
        header: "Ostvareno / očekivano",
        cell: (c) => {
          const v = c.getValue<number | null>();
          if (v == null) return <span className="text-dim">-</span>;
          return `${v < UNDERPERFORM * 100 ? "▼ " : ""}${fmtPct(v, 0)}`;
        },
        sortUndefined: "last",
        meta: { numeric: true, filter: "max", width: 170 },
      },
      { accessorKey: "notes", header: "Napomena", cell: (c) => c.getValue<string | null>() ?? "", meta: { filter: "text", width: 220 } },
    ],
    [],
  );

  const onRows = useCallback((r: Row[]) => setShown(r), []);
  const totExp = shown.reduce((a, r) => a + (r.expected_profit ?? 0), 0);
  const totAct = shown.reduce((a, r) => a + (r.expected_profit != null ? r.actual_profit : 0), 0);
  const under = shown.filter((r) => r.ratio_pct != null && r.ratio_pct < UNDERPERFORM * 100).length;

  const footer = error ? (
    <span role="alert" className="font-mono text-[12px] text-sodium">
      {"✕"} {error}
    </span>
  ) : (
    <div className="flex gap-8 font-mono text-[12px]">
      <span>
        <span className="font-sans text-dim">Očekivano (sa planom) </span>
        {fmtNum(totExp)}
      </span>
      <span>
        <span className="font-sans text-dim">Ostvareno </span>
        {fmtNum(totAct)} {totExp > 0 && <span className="text-dim">({fmtPct((totAct / totExp) * 100, 0)})</span>}
      </span>
      <span className={under ? "text-sodium" : "text-dim"}>
        {under ? `▼ ${under} ispod 80% očekivanog` : "nijedan ispod 80%"}
      </span>
    </div>
  );

  return (
    <Screen footer={footer}>
      <DataTable
        data={rows}
        columns={columns}
        initialSort={[{ id: "logged_at", desc: true }]}
        rowMark={(r) => (r.ratio_pct != null && r.ratio_pct < UNDERPERFORM * 100 ? "bad" : undefined)}
        getRowId={(r) => r.trade_id}
        onRowsChange={onRows}
        empty={trades ? "Nema unetih trejdova. Unos: `eve log-trade`." : "Učitavanje…"}
      />
    </Screen>
  );
}
