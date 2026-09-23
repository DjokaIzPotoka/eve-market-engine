import type { DiskSample } from "../types";
import { fmtDay, fmtNum } from "../lib/format";

/** Stepped line, no fill/gradient, dashed warn threshold, dot at "now". */
export function DiskChart({ samples, warnPct }: { samples: DiskSample[]; warnPct: number }) {
  const W = 420;
  const H = 130;
  const padL = 34;
  const padR = 8;
  const padT = 8;
  const padB = 18;
  const pts = samples.map((s) => ({
    t: Date.parse(s.sampled_at),
    p: (s.used_bytes / s.total_bytes) * 100,
  }));
  if (pts.length < 2) return <p className="text-dim">Nema uzoraka: ingest još ne beleži zauzeće diska.</p>;
  const t0 = pts[0].t;
  const t1 = pts[pts.length - 1].t;
  const yMax = 100;
  const x = (t: number) => padL + ((t - t0) / (t1 - t0)) * (W - padL - padR);
  const y = (p: number) => padT + (1 - p / yMax) * (H - padT - padB);

  let d = `M${x(pts[0].t).toFixed(1)},${y(pts[0].p).toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) {
    d += ` H${x(pts[i].t).toFixed(1)} V${y(pts[i].p).toFixed(1)}`;
  }
  const last = pts[pts.length - 1];
  const ticks = [0, 50, 100];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      role="img"
      aria-label={`Zauzeće diska, trenutno ${fmtNum(last.p, 0)}%, prag ${warnPct}%`}
    >
      {ticks.map((tk) => (
        <g key={tk}>
          <line x1={padL} x2={W - padR} y1={y(tk)} y2={y(tk)} stroke="var(--color-bulkhead)" strokeWidth={1} />
          <text x={padL - 5} y={y(tk) + 4} textAnchor="end" fontSize={10} fill="var(--color-dim)" fontFamily="var(--font-mono)">
            {tk}%
          </text>
        </g>
      ))}
      <line
        x1={padL}
        x2={W - padR}
        y1={y(warnPct)}
        y2={y(warnPct)}
        stroke="var(--color-sodium)"
        strokeDasharray="4 3"
        strokeWidth={1}
      />
      <text x={W - padR} y={y(warnPct) - 3} textAnchor="end" fontSize={10} fill="var(--color-sodium)" fontFamily="var(--font-mono)">
        prag {warnPct}%
      </text>
      <path d={d} fill="none" stroke="var(--color-telemetry)" strokeWidth={1.5} />
      <circle cx={x(last.t)} cy={y(last.p)} r={3} fill="var(--color-telemetry)" />
      <text x={x(last.t) - 6} y={y(last.p) - 6} textAnchor="end" fontSize={11} fill="var(--color-readout)" fontFamily="var(--font-mono)">
        {fmtNum(last.p, 0)}%
      </text>
      <text x={padL} y={H - 4} fontSize={10} fill="var(--color-dim)" fontFamily="var(--font-mono)">
        {fmtDay(samples[0].sampled_at)}
      </text>
      <text x={W - padR} y={H - 4} textAnchor="end" fontSize={10} fill="var(--color-dim)" fontFamily="var(--font-mono)">
        {fmtDay(samples[samples.length - 1].sampled_at)}
      </text>
    </svg>
  );
}
