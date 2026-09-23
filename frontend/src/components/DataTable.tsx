import { useEffect, useMemo, useRef, useState } from "react";
import {
  type ColumnDef,
  type ColumnFiltersState,
  type FilterFn,
  type Row,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    /** numbers right-aligned, monospace */
    numeric?: boolean;
    /** which filter control appears in the second sticky header row */
    filter?: "text" | "min" | "max" | "select";
    options?: string[];
    /** fixed column width in px */
    width?: number;
    /** paint this cell with the row's marker color (the "key" cell) */
    key?: boolean;
  }
}

const ROW_H = 24;
const HEAD_H = 28;
const FILTER_H = 26;
const VIRTUALIZE_ABOVE = 200;

export type RowMark = "good" | "bad" | undefined;

const textFilter: FilterFn<unknown> = (row, id, value: string) =>
  String(row.getValue(id)).toLowerCase().includes(value.toLowerCase());
const minFilter: FilterFn<unknown> = (row, id, value: string) =>
  value === "" || Number(row.getValue(id)) >= Number(value);
const maxFilter: FilterFn<unknown> = (row, id, value: string) =>
  value === "" || Number(row.getValue(id)) <= Number(value);
const selectFilter: FilterFn<unknown> = (row, id, value: string) =>
  value === "" || row.getValue(id) === value;
const filterFns = { text: textFilter, min: minFilter, max: maxFilter, select: selectFilter };

interface Props<T> {
  data: T[];
  columns: ColumnDef<T, any>[];
  initialSort?: SortingState;
  /** "good" = actionable (left rule + tint in Telemetry); "bad" = underperforming (Sodium). */
  rowMark?: (row: T) => RowMark;
  getRowId?: (row: T, index: number) => string;
  /** rendered in the status line under the table; receives visible rows after filtering */
  onRowsChange?: (rows: T[]) => void;
  empty?: string;
}

export function DataTable<T>({ data, columns, initialSort, rowMark, getRowId, onRowsChange, empty }: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSort ?? []);
  const [filters, setFilters] = useState<ColumnFiltersState>([]);
  const [active, setActive] = useState<number>(-1);
  const scrollRef = useRef<HTMLDivElement>(null);

  const cols = useMemo(
    () =>
      columns.map((c) => {
        const f = c.meta?.filter;
        return f && !c.filterFn ? { ...c, filterFn: filterFns[f] as FilterFn<T> } : c;
      }),
    [columns],
  );

  const table = useReactTable({
    data,
    columns: cols,
    state: { sorting, columnFilters: filters },
    onSortingChange: setSorting,
    onColumnFiltersChange: setFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId,
    enableSortingRemoval: false,
  });

  const rows = table.getRowModel().rows;
  const virtual = rows.length > VIRTUALIZE_ABOVE;

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
    enabled: virtual,
    // The two sticky header rows sit inside the scroll area.
    scrollPaddingStart: HEAD_H + FILTER_H,
  });

  const visibleRows = useMemo(() => rows.map((r) => r.original), [rows]);
  useEffect(() => onRowsChange?.(visibleRows), [visibleRows, onRowsChange]);

  const items = virtual ? virtualizer.getVirtualItems() : rows.map((_, i) => ({ index: i, start: i * ROW_H }));
  const padTop = virtual && items.length ? items[0].start : 0;
  const padBottom = virtual && items.length ? virtualizer.getTotalSize() - items[items.length - 1].start - ROW_H : 0;

  function move(delta: number) {
    const next = Math.min(rows.length - 1, Math.max(0, active + delta));
    setActive(next);
    if (virtual) virtualizer.scrollToIndex(next);
    else scrollRef.current?.querySelector(`[data-row="${next}"]`)?.scrollIntoView({ block: "nearest" });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    const t = e.target as HTMLElement;
    if (t.tagName === "INPUT" || t.tagName === "SELECT") return;
    if (e.key === "j" || e.key === "ArrowDown") (e.preventDefault(), move(1));
    else if (e.key === "k" || e.key === "ArrowUp") (e.preventDefault(), move(-1));
    else if (e.key === "/") {
      e.preventDefault();
      scrollRef.current?.querySelector<HTMLInputElement>("thead input")?.focus();
    }
  }

  const headerGroup = table.getHeaderGroups()[0];
  const hasFilters = headerGroup.headers.some((h) => h.column.columnDef.meta?.filter);

  return (
    <div
      ref={scrollRef}
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="h-full min-h-0 overflow-auto bg-hull"
      aria-label="Tabela"
    >
      <table className="w-max min-w-full border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            {headerGroup.headers.map((h, i) => {
              const m = h.column.columnDef.meta;
              const dir = h.column.getIsSorted();
              return (
                <th
                  key={h.id}
                  style={{ height: HEAD_H, width: m?.width, minWidth: m?.width }}
                  aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
                  className={`sticky top-0 z-20 border-b border-bulkhead bg-deck p-0 text-[12px] font-medium text-dim ${i === 0 ? "left-0 z-30" : ""}`}
                >
                  <button
                    type="button"
                    onClick={h.column.getToggleSortingHandler()}
                    className={`flex h-full w-full cursor-pointer items-center gap-1 px-2 hover:text-readout ${m?.numeric ? "justify-end" : "justify-start"} ${dir ? "text-readout" : ""}`}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    <span aria-hidden className="w-2 text-[10px]">
                      {dir === "asc" ? "▲" : dir === "desc" ? "▼" : ""}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
          {hasFilters && (
            <tr>
              {headerGroup.headers.map((h, i) => {
                const m = h.column.columnDef.meta;
                const val = (h.column.getFilterValue() as string) ?? "";
                return (
                  <th
                    key={h.id}
                    style={{ top: HEAD_H, height: FILTER_H }}
                    className={`sticky z-20 border-b border-bulkhead bg-deck px-1 ${i === 0 ? "left-0 z-30" : ""}`}
                  >
                    {m?.filter === "select" ? (
                      <select
                        aria-label={`Filter ${String(h.column.columnDef.header)}`}
                        value={val}
                        onChange={(e) => h.column.setFilterValue(e.target.value)}
                        className="h-5 w-full border border-bulkhead bg-hull px-1 font-mono text-[12px]"
                      >
                        <option value="">sve</option>
                        {m.options?.map((o) => (
                          <option key={o}>{o}</option>
                        ))}
                      </select>
                    ) : m?.filter ? (
                      <input
                        aria-label={`Filter ${String(h.column.columnDef.header)}`}
                        value={val}
                        inputMode={m.filter === "text" ? "text" : "decimal"}
                        placeholder={m.filter === "min" ? "≥" : m.filter === "max" ? "≤" : "filter"}
                        onChange={(e) => h.column.setFilterValue(e.target.value)}
                        className={`h-5 w-full border border-bulkhead bg-hull px-1 font-mono text-[12px] placeholder:text-dim ${m.numeric ? "text-right" : ""}`}
                      />
                    ) : null}
                  </th>
                );
              })}
            </tr>
          )}
        </thead>
        <tbody>
          {padTop > 0 && (
            <tr aria-hidden style={{ height: padTop }}>
              <td />
            </tr>
          )}
          {items.map((it) => {
            const row = rows[it.index] as Row<T>;
            const mark = rowMark?.(row.original);
            const isActive = it.index === active;
            const color = mark === "good" ? "var(--color-telemetry)" : "var(--color-sodium)";
            return (
              <tr
                key={row.id}
                data-row={it.index}
                onClick={() => setActive(it.index)}
                style={{
                  height: ROW_H,
                  background: mark
                    ? `color-mix(in srgb, ${color} 7%, transparent)`
                    : isActive
                      ? "rgb(211 220 230 / 0.06)"
                      : undefined,
                }}
                className="hover:bg-readout/5"
              >
                {row.getVisibleCells().map((cell, i) => {
                  const m = cell.column.columnDef.meta;
                  const first = i === 0;
                  return (
                    <td
                      key={cell.id}
                      style={{
                        height: ROW_H,
                        boxShadow: first && mark ? `inset 2px 0 0 ${color}` : undefined,
                        color: m?.key && mark ? color : undefined,
                      }}
                      className={`overflow-hidden border-b border-bulkhead/60 px-2 leading-[23px] whitespace-nowrap ${first ? "sticky left-0 z-10 bg-hull" : ""} ${
                        m?.numeric ? "text-right font-mono" : ""
                      } ${m?.key && mark ? "font-semibold" : ""} ${isActive && first ? "outline outline-1 -outline-offset-1 outline-readout/60" : ""}`}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {padBottom > 0 && (
            <tr aria-hidden style={{ height: padBottom }}>
              <td />
            </tr>
          )}
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-dim">
                {empty ?? "Nema redova za zadate filtere."}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
