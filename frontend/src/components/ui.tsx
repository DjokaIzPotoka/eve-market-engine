import type { ReactNode } from "react";
import { clampPct } from "../lib/format";

/** Panel with its title cut into the top border, like a legend on an instrument. */
export function Panel({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={`panel px-3 pt-4 pb-3 ${className}`}>
      <h2 className="absolute -top-[9px] left-3 bg-deck px-1.5 text-[13px] font-semibold text-readout">{title}</h2>
      {children}
    </section>
  );
}

export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-bulkhead/50 py-1 last:border-b-0">
      <dt className="text-dim">{k}</dt>
      <dd className="font-mono">{children}</dd>
    </div>
  );
}

/** Text token instead of a rounded pill. */
export function Token({ tone, children }: { tone: "ok" | "warn"; children: ReactNode }) {
  return (
    <span className={`font-mono text-[12px] ${tone === "ok" ? "text-telemetry" : "text-sodium"}`}>
      [{children}]
    </span>
  );
}

/**
 * Segmented meter: 50 cells of 2%. Value is clamped to 100; the warn tick sits at `warnAt`.
 * Above `warnAt` the fill turns Sodium.
 */
export function SegBar({
  pct,
  warnAt = 90,
  lowIsBad = false,
  label,
}: {
  pct: number;
  warnAt?: number;
  /** for "remaining" meters: warn when the value falls to `warnAt` or below */
  lowIsBad?: boolean;
  label: string;
}) {
  const v = clampPct(pct);
  const cells = 50;
  const filled = Math.round((v / 100) * cells);
  const warn = lowIsBad ? v <= warnAt : v >= warnAt;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v)}
      className="relative flex h-3 w-full gap-px"
    >
      {Array.from({ length: cells }, (_, i) => (
        <span
          key={i}
          className="h-full flex-1"
          style={{
            background:
              i < filled ? (warn ? "var(--color-sodium)" : "var(--color-telemetry)") : "var(--color-bulkhead)",
          }}
        />
      ))}
      <span
        aria-hidden
        className="absolute -top-1 -bottom-1 w-px bg-readout/70"
        style={{ left: `${warnAt}%` }}
      />
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[12px] text-dim">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-dim">{hint}</span>}
    </label>
  );
}

export function NumInput({
  value,
  onChange,
  width = 120,
  step,
  min,
  max,
  ...rest
}: {
  value: number;
  onChange: (n: number) => void;
  width?: number;
  step?: number;
  min?: number;
  max?: number;
  "aria-label"?: string;
}) {
  return (
    <input
      type="number"
      className="field text-right"
      style={{ width }}
      value={Number.isFinite(value) ? value : ""}
      step={step}
      min={min}
      max={max}
      onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
      {...rest}
    />
  );
}

/** Screen scaffold: fixed toolbar on top, table filling the rest, fixed footer below. */
export function Screen({
  toolbar,
  footer,
  children,
}: {
  toolbar?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {toolbar && <div className="shrink-0 border-b border-bulkhead bg-deck px-3 py-2">{toolbar}</div>}
      <div className="min-h-0 flex-1">{children}</div>
      {footer && <div className="shrink-0 border-t border-bulkhead bg-deck px-3 py-2">{footer}</div>}
    </div>
  );
}
