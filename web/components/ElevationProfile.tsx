"use client";

import { useState } from "react";
import { n0 } from "@/lib/format";

type Props = {
  distance: number[];
  altitude: number[];
  heartRate: number[] | null;
  colors: string[];
  highlight: [number, number] | null;
};

const W = 1000, H = 180, PAD = { l: 64, r: 8, t: 8, b: 24 };

export default function ElevationProfile({ distance, altitude, heartRate, colors, highlight }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const dMax = distance[distance.length - 1] || 1;
  const aMin = Math.min(...altitude), aMax = Math.max(...altitude);
  const span = Math.max(aMax - aMin, 100);
  const x = (d: number) => PAD.l + (d / dMax) * (W - PAD.l - PAD.r);
  const y = (a: number) => PAD.t + (1 - (a - aMin) / span) * (H - PAD.t - PAD.b);
  const base = H - PAD.b;

  // One filled polygon per run of the same color.
  const runs: { color: string; from: number; to: number }[] = [];
  colors.forEach((c, i) => {
    const last = runs[runs.length - 1];
    if (last && last.color === c) last.to = i;
    else runs.push({ color: c, from: Math.max(0, i - 1), to: i });
  });
  const poly = (from: number, to: number) => {
    let p = `M${x(distance[from])},${base}`;
    for (let i = from; i <= to; i++) p += ` L${x(distance[i])},${y(altitude[i])}`;
    return p + ` L${x(distance[to])},${base} Z`;
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * dMax);

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const d = (((e.clientX - rect.left) / rect.width) * W - PAD.l) / (W - PAD.l - PAD.r) * dMax;
    let lo = 0, hi = distance.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (distance[mid] < d) lo = mid + 1; else hi = mid;
    }
    setHover(Math.min(Math.max(lo, 0), distance.length - 1));
  }

  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block", touchAction: "none" }}
        role="img" aria-label="Elevation profile in feet over miles, shaded by the selected measure"
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[aMin, aMin + span / 2, aMin + span].map((a) => (
          <g key={a}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(a)} y2={y(a)} stroke="var(--line)" />
            <text x={PAD.l - 6} y={y(a) + 4} textAnchor="end" fontSize={12} fill="var(--muted)">{a === aMin + span ? `${n0(a)} ft` : n0(a)}</text>
          </g>
        ))}
        {highlight ? (
          <rect x={x(distance[highlight[0]])} y={PAD.t} width={Math.max(2, x(distance[highlight[1]]) - x(distance[highlight[0]]))}
            height={base - PAD.t} fill="var(--highlight)" opacity={0.25} />
        ) : null}
        {runs.map((r, i) => <path key={i} d={poly(r.from, r.to)} fill={r.color} stroke="var(--surface)" strokeWidth={0.5} />)}
        {ticks.map((t, i) => (
          <text key={i} x={x(t)} y={H - 6} fontSize={12} fill="var(--muted)" textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"}>
            {i === 4 ? `${t.toFixed(1)} mi` : t.toFixed(1)}
          </text>
        ))}
        {hover != null ? (
          <g>
            <line x1={x(distance[hover])} x2={x(distance[hover])} y1={PAD.t} y2={base} stroke="var(--ink)" strokeWidth={1} />
            <circle cx={x(distance[hover])} cy={y(altitude[hover])} r={4} fill="var(--surface)" stroke="var(--ink)" strokeWidth={2} />
          </g>
        ) : null}
      </svg>
      {hover != null ? (
        <div className="tip" style={{ position: "absolute", top: 0, left: `${(x(distance[hover]) / W) * 100}%`, transform: "translateX(-50%)", pointerEvents: "none" }}>
          <div className="mono">{distance[hover].toFixed(2)} mi, {n0(altitude[hover])} ft</div>
          {heartRate ? <div className="mono">{n0(heartRate[hover])} bpm</div> : null}
        </div>
      ) : null}
    </div>
  );
}
