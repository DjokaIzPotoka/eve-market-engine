import { DiskChart } from "../components/DiskChart";
import { KV, Panel, SegBar, Token } from "../components/ui";
import { fmtNum, fmtRelative, fmtTimestamp } from "../lib/format";
import { useStatus } from "../lib/status";

export function StatusScreen() {
  const { status, nowMs, healthy, headline, error } = useStatus();
  if (!status)
    return error ? (
      <p role="alert" className="m-4 border border-sodium bg-sodium/10 px-3 py-2 font-mono text-sodium">
        {"✕"} {error}
      </p>
    ) : (
      <p className="p-4 text-dim">Učitavanje…</p>
    );

  const esi = status.esi_limit;
  const esiPct = esi ? (esi.remain / status.esi_limit_max) * 100 : 0;
  const esiLow = esi ? esiPct <= 20 : false;

  return (
    <div className="flex flex-col gap-5 p-4">
      <div
        role="status"
        className={`flex items-baseline gap-4 border px-3 py-2 ${
          healthy ? "border-telemetry/60 text-telemetry" : "border-sodium bg-sodium/10 text-sodium"
        }`}
      >
        <span className="font-mono text-[18px] font-semibold">
          {healthy ? "●" : "▲"} {headline}
        </span>
        <span className="ml-auto font-mono text-[12px] text-dim">
          prag zastarelosti {status.stale_after_minutes} min
        </span>
      </div>

      <div className="grid grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] gap-x-5 gap-y-6">
        <Panel title="Poslednji snapshot">
          <ul className="flex flex-col">
            {status.regions.map((r) => (
              <li key={r.region_id} className="border-b border-bulkhead/50 py-2 first:pt-0 last:border-b-0 last:pb-0">
                <div className="flex items-baseline gap-3">
                  <span className="text-[14px] font-semibold">{r.region_name}</span>
                  <span className="font-mono text-[12px] text-dim">{r.region_id}</span>
                  <span className="ml-auto">
                    <Token tone={r.stale ? "warn" : "ok"}>{r.stale ? "STALE" : "OK"}</Token>
                  </span>
                </div>
                {r.last_snapshot ? (
                  <dl className="mt-1">
                    <KV k="pre">
                      <span className="text-[16px]">{fmtRelative(r.last_snapshot.fetched_at, nowMs)}</span>
                    </KV>
                    <KV k="timestamp (UTC)">{fmtTimestamp(r.last_snapshot.fetched_at)}</KV>
                    <KV k="orderi">{fmtNum(r.last_snapshot.order_count)}</KV>
                    <KV k="snapshot_id">
                      <span className="text-dim">{r.last_snapshot.snapshot_id}</span>
                    </KV>
                  </dl>
                ) : (
                  <p className="mt-1 text-sodium">Nema snapshota.</p>
                )}
              </li>
            ))}
          </ul>
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel title="ESI error limit">
            {esi ? (
              <>
                <div className="flex items-baseline gap-2 pb-2">
                  <span className={`font-mono text-[22px] font-semibold ${esiLow ? "text-sodium" : ""}`}>
                    {esi.remain}
                  </span>
                  <span className="font-mono text-dim">/ {status.esi_limit_max}</span>
                  <span className="ml-auto font-mono text-[12px] text-dim">reset za {Math.max(0, Math.round(esi.reset_s - (nowMs - Date.parse(esi.sampled_at)) / 1000))}s · uzorak pre {fmtRelative(esi.sampled_at, nowMs)}</span>
                </div>
                <SegBar pct={esiPct} warnAt={20} lowIsBad label="ESI error limit preostalo" />
              </>
            ) : (
              <p className="pb-1 text-dim">Nema uzoraka: ingest još nije zabeležio ESI limit.</p>
            )}
            <dl className="mt-2">
              <KV k="ingest run (24h)">
                {fmtNum(status.runs_24h_ok)} ok
                {status.runs_24h_failed > 0 && (
                  <span className="text-sodium"> · {status.runs_24h_failed} {"✕"}</span>
                )}
              </KV>
              <KV k="history (dnevno)">
                {status.history_last_ok_at ? `${fmtTimestamp(status.history_last_ok_at)} ok` : "-"}
              </KV>
            </dl>
          </Panel>

          <Panel title="Zauzeće diska (14 dana)">
            <DiskChart samples={status.disk} warnPct={status.disk_warn_pct} />
          </Panel>
        </div>

        <Panel title="Poslednje greške" className="col-span-2">
          {status.recent_errors.length === 0 ? (
            <p className="text-dim">Nema grešaka u poslednja 24h.</p>
          ) : (
            <table className="w-full border-separate border-spacing-0 text-[13px]">
              <thead>
                <tr className="text-left text-[12px] text-dim">
                  <th className="pr-4 pb-1 font-medium">vreme (UTC)</th>
                  <th className="pr-4 pb-1 font-medium">komanda</th>
                  <th className="pr-4 pb-1 text-right font-medium">region</th>
                  <th className="pb-1 pl-4 font-medium">poruka</th>
                </tr>
              </thead>
              <tbody>
                {status.recent_errors.map((e) => (
                  <tr key={e.run_id}>
                    <td className="border-t border-bulkhead/50 py-1 pr-4 font-mono whitespace-nowrap">
                      {fmtTimestamp(e.finished_at)}
                    </td>
                    <td className="border-t border-bulkhead/50 py-1 pr-4 font-mono">{e.command}</td>
                    <td className="border-t border-bulkhead/50 py-1 pr-4 text-right font-mono">{e.region_id}</td>
                    <td className="border-t border-bulkhead/50 py-1 pl-4 font-mono text-sodium">
                      {"✕"} {e.error_message}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}
