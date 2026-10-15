"use client";

import { Bar, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from "recharts";
import { dayLabel } from "@/lib/format";
import type { HealthNight } from "@/lib/types";

export type HealthMetric = "hrv" | "resting_hr" | "sleep_h";

const SPEC: Record<HealthMetric, { color: string; unit: string; fmt: (v: number) => string; label: string }> = {
  hrv: { color: "var(--cardio)", unit: "ms", fmt: (v) => `${Math.round(v)} ms`, label: "HRV" },
  resting_hr: { color: "var(--descent)", unit: "bpm", fmt: (v) => `${Math.round(v)} bpm`, label: "Resting HR" },
  sleep_h: { color: "var(--climb)", unit: "h", fmt: (v) => `${Math.floor(v)} h ${String(Math.round((v % 1) * 60)).padStart(2, "0")}`, label: "Sleep" },
};

/** One overnight signal over the range, oldest on the left. HRV: each night as a dot, the 7-day
 * average and the 60-day baseline as lines. Resting HR: each night and its baseline. Sleep: a bar
 * a night against the stated need. */
export default function HealthChart({ nights, metric, needH }: { nights: HealthNight[]; metric: HealthMetric; needH?: number }) {
  const s = SPEC[metric];
  const data = [...nights].reverse();
  const baseline = metric === "hrv" ? "hrv_baseline" : metric === "resting_hr" ? "resting_hr_baseline" : null;
  return (
    <div role="img" aria-label={`${s.label} each night over the range`} style={{ height: 180 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
          <XAxis dataKey="day" tickFormatter={(d) => dayLabel(String(d), { month: "short", day: "numeric" })}
            tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={{ stroke: "var(--line)" }} minTickGap={40} />
          <YAxis width={36} tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false}
            domain={metric === "sleep_h" ? [0, "auto"] : ["auto", "auto"]} allowDecimals={false} />
          <Tooltip
            cursor={{ stroke: "var(--line-strong)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const n = payload[0].payload as HealthNight;
              const v = n[metric];
              const base = baseline ? n[baseline] : null;
              return (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>{dayLabel(n.day, { weekday: "short", month: "short", day: "numeric" })}</div>
                  <div>{s.label} <span className="mono">{v == null ? "no reading" : s.fmt(v)}</span></div>
                  {metric === "hrv" && n.hrv_avg_7d != null ? <div>7-day average <span className="mono">{s.fmt(n.hrv_avg_7d)}</span></div> : null}
                  {base != null ? <div>60-day baseline <span className="mono">{s.fmt(base)}</span></div> : null}
                  {metric === "sleep_h" && needH ? <div>Need <span className="mono">{s.fmt(needH)}</span></div> : null}
                </div>
              );
            }}
          />
          {metric === "sleep_h" && needH ? <ReferenceLine y={needH} stroke="var(--muted)" strokeDasharray="4 4" /> : null}
          {metric === "sleep_h" ? (
            <Bar dataKey="sleep_h" fill={s.color} fillOpacity={0.75} radius={[2, 2, 0, 0]} isAnimationActive={false} />
          ) : metric === "hrv" ? (
            <>
              <Scatter dataKey="hrv" fill={s.color} fillOpacity={0.45} shape="circle" isAnimationActive={false} />
              <Line dataKey="hrv_avg_7d" stroke={s.color} strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
            </>
          ) : (
            <Line dataKey="resting_hr" stroke={s.color} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          )}
          {baseline ? <Line dataKey={baseline} stroke="var(--muted)" strokeWidth={1.5} strokeDasharray="4 4" dot={false} connectNulls isAnimationActive={false} /> : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
