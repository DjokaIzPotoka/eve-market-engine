import { useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { fmtNum, fmtRelative } from "../lib/format";
import { useStatus } from "../lib/status";
import { fmtBytesPctSafe } from "./shellUtil";

const NAV = [
  { to: "/", label: "Status", key: "s", health: true },
  { to: "/hauling", label: "Hauling", key: "h" },
  { to: "/market-making", label: "Market Making", key: "m" },
  { to: "/trades", label: "Moji trejdovi", key: "t" },
  { to: "/settings", label: "Podešavanja", key: "p" },
];

/** `g` then a letter jumps to a screen; ignored while typing in a field. */
function useGoHotkeys() {
  const navigate = useNavigate();
  useEffect(() => {
    let armed = false;
    let timer: ReturnType<typeof setTimeout>;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA") return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (armed) {
        const hit = NAV.find((n) => n.key === e.key);
        if (hit) navigate(hit.to);
        armed = false;
        clearTimeout(timer);
      } else if (e.key === "g") {
        armed = true;
        timer = setTimeout(() => (armed = false), 1200);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
}

function TopStrip() {
  const { status, nowMs, healthy } = useStatus();
  const newest = status?.regions
    .map((r) => r.last_snapshot?.fetched_at)
    .filter((v): v is string => !!v)
    .sort()
    .at(-1);
  const disk = status ? fmtBytesPctSafe(status.disk) : null;
  const esiLow = status?.esi_limit ? status.esi_limit.remain <= status.esi_limit_max * 0.2 : false;
  const diskHigh = status && disk != null ? disk >= status.disk_warn_pct : false;

  return (
    <div className="flex h-8 shrink-0 items-center gap-5 border-b border-bulkhead bg-deck px-3 font-mono text-[12px]">
      <span className="flex items-center gap-2">
        <span
          aria-hidden
          className={`inline-block h-2 w-2 ${healthy ? "bg-telemetry" : "bg-sodium pulse"}`}
        />
        <span className={healthy ? "text-telemetry" : "text-sodium"}>{healthy ? "OK" : "PAŽNJA"}</span>
      </span>
      <span>
        <span className="text-dim">SNAP </span>
        {newest ? `pre ${fmtRelative(newest, nowMs)}` : "-"}
      </span>
      <span className={esiLow ? "text-sodium" : ""}>
        <span className="text-dim">ESI </span>
        {status?.esi_limit ? `${status.esi_limit.remain}/${status.esi_limit_max}` : "-"}
      </span>
      <span className={diskHigh ? "text-sodium" : ""}>
        <span className="text-dim">DISK </span>
        {disk != null ? `${fmtNum(disk)}%` : "-"}
      </span>
      <span className="ml-auto text-dim">
        {new Date(nowMs).toISOString().slice(11, 19)}Z
      </span>
    </div>
  );
}

export function Shell() {
  useGoHotkeys();
  const { healthy } = useStatus();
  return (
    <div className="flex h-full">
      <nav aria-label="Navigacija" className="flex w-[168px] shrink-0 flex-col border-r border-bulkhead bg-deck">
        <div className="border-b border-bulkhead px-3 py-2.5 text-[15px] font-semibold tracking-wide">
          EVE Market Engine
        </div>
        <ul className="flex flex-col py-1">
          {NAV.map((n) => (
            <li key={n.to}>
              <NavLink
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) =>
                  `flex h-8 items-center justify-between px-3 text-[14px] hover:bg-readout/5 ${
                    isActive ? "bg-readout/8 font-semibold shadow-[inset_2px_0_0_var(--color-readout)]" : "text-dim"
                  }`
                }
              >
                <span className="flex items-center gap-2">
                  {n.label}
                  {n.health && (
                    <span
                      aria-label={healthy ? "zdravo" : "upozorenje"}
                      className={`inline-block h-1.5 w-1.5 ${healthy ? "bg-telemetry" : "bg-sodium"}`}
                    />
                  )}
                </span>
                <span className="font-mono text-[11px] text-dim">g {n.key}</span>
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="mt-auto border-t border-bulkhead px-3 py-2 text-[11px] leading-snug text-dim">
          j/k red · / filter
          <br />g + slovo: ekran
        </p>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopStrip />
        <main className="min-h-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
