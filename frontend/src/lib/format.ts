// Comma as thousands separator (decimals stay a dot): 1,234,567.89
function group(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function fmtNum(n: number, decimals = 2): string {
  if (!Number.isFinite(n)) return "-";
  const neg = n < 0 && Number(Math.abs(n).toFixed(decimals)) !== 0;
  const [i, d] = Math.abs(n).toFixed(decimals).split(".");
  return (neg ? "−" : "") + group(i) + (d ? "." + d : "");
}

/** Signed with a glyph so sign never relies on color alone. */
export function fmtSigned(n: number, decimals = 2): string {
  const s = fmtNum(n, decimals);
  return n > 0 && s !== fmtNum(0, decimals) ? "+" + s : s;
}

export const fmtIsk = (n: number) => fmtNum(n, 2);

/** Counts (orders, rows, runs) are whole numbers: no decimals. */
export const fmtInt = (n: number) => fmtNum(n, 0);
export const fmtPct = (n: number, d = 2) => fmtNum(n, d) + "%";

export function fmtTimestamp(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+/, "");
}

export function fmtDay(iso: string): string {
  return iso.slice(0, 10);
}

export function fmtRelative(iso: string, now: number = Date.now()): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d`;
}

/** Progress values are clamped: a bar never renders past 100%. */
export const clampPct = (n: number) => Math.min(100, Math.max(0, n));
