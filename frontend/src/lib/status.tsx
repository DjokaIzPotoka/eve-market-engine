import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { StatusReport } from "../types";

interface Ctx {
  status: StatusReport | null;
  /** server-time "now" that ticks locally between polls */
  nowMs: number;
  healthy: boolean;
  headline: string;
  /** last polling error (API down); status keeps the previous report if it has one */
  error: string | null;
}

const StatusCtx = createContext<Ctx>({ status: null, nowMs: Date.now(), healthy: true, headline: "", error: null });
export const useStatus = () => useContext(StatusCtx);

const POLL_MS = 30_000;

export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<StatusReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [anchor, setAnchor] = useState({ server: Date.now(), local: Date.now() });
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.getStatus().then(
        (s) => {
          if (!alive) return;
          setStatus(s);
          setError(null);
          setAnchor({ server: Date.parse(s.now), local: Date.now() });
        },
        (e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)),
      );
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  void tick;
  const nowMs = anchor.server + (Date.now() - anchor.local);

  let healthy = true;
  let headline = "Učitavanje…";
  if (error) {
    healthy = false;
    headline = `PAŽNJA: ${error}`;
  } else if (status) {
    const stale = status.regions.filter((r) => r.stale);
    const errs = status.recent_errors.length;
    healthy = stale.length === 0 && errs === 0;
    if (stale.length) {
      const r = stale[0];
      headline = r.last_snapshot
        ? `PAŽNJA: snapshot ${r.region_name} star ${Math.round((nowMs - Date.parse(r.last_snapshot.fetched_at)) / 60000)} min`
        : `PAŽNJA: nema snapshota za ${r.region_name}`;
    } else if (errs) {
      headline = `PAŽNJA: ${errs} ${errs === 1 ? "greška" : "grešaka"} u poslednja 24h`;
    } else {
      headline = "SISTEM ISPRAVAN";
    }
  }

  return <StatusCtx.Provider value={{ status, nowMs, healthy, headline, error }}>{children}</StatusCtx.Provider>;
}
