"use client";

import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { type ChartDay, dayLabel, ledgerNum, n0 } from "@/lib/format";
import type { ProjectedDay } from "@/lib/types";

type Row = Partial<ChartDay> & { day: string; pfitness?: number; pfatigue?: number; band?: [number, number] };

// The shaded band runs from your normal up to the ramp line: a dashed line above it is ramping too fast.
const RAMP_RATIO = 1.3;

// `decimals` and `unit` set how numbers read: distance is "4.2 mi", the other loads whole numbers.
// `projected` carries both lines forward through scheduled runs, drawn dotted.
export default function LedgerChart({ series, projected = [], color, label, decimals = 0, unit = "", height = 180 }: {
  series: ChartDay[]; projected?: ProjectedDay[]; color: string; label: string; decimals?: number; unit?: string; height?: number;
}) {
  const num = (v: number) => ledgerNum(v, decimals) + unit;
  const ahead = projected.filter((p) => !series.length || p.day > series[series.length - 1].day);
  const rows: Row[] = series.map((d) => ({ ...d }));
  if (ahead.length && rows.length) {
    const last = rows[rows.length - 1];
    last.pfitness = last.fitness;
    last.pfatigue = last.fatigue;
  }
  for (const p of ahead) rows.push({ day: p.day, pfitness: p.fitness, pfatigue: p.fatigue });
  for (const r of rows) {
    const f = r.fitness ?? r.pfitness;
    if (f != null) r.band = [f, f * RAMP_RATIO];
  }
  // Fixed ticks, so labels never repeat: month starts on long ranges, Mondays on short ones.
  const long = rows.length > 70;
  const ticks = rows.map((r) => r.day).filter((d) => (rows.length > 200 ? d.endsWith("-01")
    : long ? d.endsWith("-01") || d.endsWith("-15") : new Date(`${d}T12:00`).getDay() === 1));
  return (
    <div role="img" aria-label={`${label} load: your normal (solid) and last 7 days (dashed), with a band up to the ramp line${ahead.length ? ", then where your scheduled runs take them (dotted)" : ""}`} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--line)" />
          <XAxis dataKey="day" tickLine={false} axisLine={{ stroke: "var(--line)" }} ticks={ticks} interval="preserveStartEnd" minTickGap={16}
            tick={{ fontSize: 12, fill: "var(--muted)" }}
            tickFormatter={(d: string) => dayLabel(d, rows.length > 200 ? { month: "short" } : { month: "short", day: "numeric" })} />
          {/* Zoomed to the lines rather than from zero, so a 20% swing reads as one. */}
          <YAxis width={48} domain={["auto", "auto"]} tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "var(--muted)" }}
            tickFormatter={(v: number) => (decimals ? String(+v.toFixed(decimals)) : n0(v))} />
          <Tooltip
            cursor={{ stroke: "var(--line-strong)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as Row;
              if (d.load === undefined) {
                return (
                  <div className="tip">
                    <div style={{ fontWeight: 600 }}>{dayLabel(d.day, { weekday: "short", month: "short", day: "numeric" })}, scheduled</div>
                    <div>Your normal <span className="mono">{num(d.pfitness ?? 0)}</span></div>
                    <div>Last 7 days <span className="mono">{num(d.pfatigue ?? 0)}</span></div>
                  </div>
                );
              }
              return (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>{dayLabel(d.day, { weekday: "short", month: "short", day: "numeric" })}</div>
                  <div>{unit ? "That day" : "Load that day"} <span className="mono">{num(d.load)}</span></div>
                  <div>Your normal <span className="mono">{num(d.fitness ?? 0)}</span></div>
                  <div>Last 7 days <span className="mono">{num(d.fatigue ?? 0)}</span></div>
                  <div>{d.vs_normal_text}</div>
                  {(d.flags ?? []).map((f) => <div key={f.key} style={{ color: "var(--warn-ink)", fontWeight: 600 }}>{f.text}</div>)}
                </div>
              );
            }}
          />
          <Area dataKey="band" stroke="none" fill={color} fillOpacity={0.1} isAnimationActive={false} activeDot={false} />
          <Line dataKey="fatigue" stroke={color} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          <Line dataKey="fitness" stroke={color} strokeWidth={2.5} dot={false} isAnimationActive={false} />
          {ahead.length ? <Line dataKey="pfatigue" stroke={color} strokeOpacity={0.6} strokeWidth={1.5} strokeDasharray="1 3" dot={false} isAnimationActive={false} /> : null}
          {ahead.length ? <Line dataKey="pfitness" stroke={color} strokeOpacity={0.6} strokeWidth={2.5} strokeDasharray="1 4" strokeLinecap="round" dot={false} isAnimationActive={false} /> : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
