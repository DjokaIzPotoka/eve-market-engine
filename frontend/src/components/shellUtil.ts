import type { DiskSample } from "../types";

/** Latest disk usage in percent, or null when there are no samples. */
export function fmtBytesPctSafe(samples: DiskSample[]): number | null {
  const last = samples.at(-1);
  return last && last.total_bytes > 0 ? (last.used_bytes / last.total_bytes) * 100 : null;
}
