"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dayLabel, mi, n0 } from "@/lib/format";
import { FOCUS_TEXT } from "@/lib/plan";
import type { PlanWeek, WeekActual } from "@/lib/types";

type Row = PlanWeek & { label: string; done?: WeekActual };

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload?.length) return null;
  const w = payload[0].payload;
  return (
    <div className="tip">
      <div style={{ fontWeight: 600 }}>Week of {dayLabel(w.start, { month: "short", day: "numeric" })} · {FOCUS_TEXT[w.focus]}</div>
      <div className="mono">{mi(w.miles)} mi · {n0(w.gain_ft)} ft up · {n0(w.loss_ft)} ft down</div>
      {w.long ? <div>Long day {mi(w.long.miles)} mi, {n0(w.long.gain_ft)} ft up</div> : null}
      {w.done ? <div>Done {mi(w.done.miles)} mi, {n0(w.done.gain_ft)} ft up</div> : null}
    </div>
  );
}

// One small chart per measure, same weeks underneath: miles, then feet up. Recovery and taper
// weeks are lighter; this week is outlined.
function Strip({ rows, k, color, label, current, fmt }: {
  rows: Row[]; k: "miles" | "gain_ft"; color: string; label: string; current: string; fmt: (v: number) => string;
}) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 12, marginBottom: 2 }}>{label}</div>
      <div style={{ height: 120 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap={2}>
            <XAxis dataKey="label" hide />
            <YAxis width={44} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "var(--muted)" }} tickCount={3} tickFormatter={fmt} />
            <Tooltip cursor={{ fill: "var(--line)" }} content={<Tip />} />
            <Bar dataKey={k} radius={[4, 4, 0, 0]} isAnimationActive={false}>
              {rows.map((w) => (
                <Cell key={w.start} fill={color} fillOpacity={w.focus === "recovery" || w.focus === "taper" || w.focus === "race" ? 0.45 : 1}
                  stroke={w.start === current ? "var(--ink)" : undefined} strokeWidth={w.start === current ? 2 : 0} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function RoadChart({ weeks, current, actuals }: { weeks: PlanWeek[]; current: string; actuals?: Record<string, WeekActual> }) {
  const rows: Row[] = weeks.map((w) => ({ ...w, label: dayLabel(w.start, { month: "short", day: "numeric" }), done: actuals?.[w.start] }));
  return (
    <div role="img" aria-label="Weekly targets to the objective: miles and feet up per week" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <Strip rows={rows} k="miles" color="var(--distance)" label="Miles a week" current={current} fmt={(v) => String(Math.round(v))} />
      <Strip rows={rows} k="gain_ft" color="var(--climb)" label="Feet up a week" current={current} fmt={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(v))} />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, paddingLeft: 44 }} className="muted">
        <span>{rows[0]?.label}</span><span>{rows[rows.length - 1]?.label}</span>
      </div>
    </div>
  );
}
