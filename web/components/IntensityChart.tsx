"use client";

import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dayLabel, pct, zoneColors, zoneKeys } from "@/lib/format";
import type { LoadData, ZoneModel } from "@/lib/types";

// Stacked time in each zone of the chosen model. The red line is the 80% target, judged on time below AeT.
export default function IntensityChart({ weeks, model }: { weeks: LoadData["intensity_by_week"]; model: ZoneModel | null }) {
  const n = model?.zones ?? 3;
  const keys = zoneKeys(n);
  const colors = zoneColors(n);
  const data = weeks.map((w) => {
    const z = w.zones ?? w;
    return {
      start: w.start, belowAet: w.share.z1,
      minutes: keys.map((k) => z.minutes[k as keyof typeof z.minutes] ?? 0),
      ...Object.fromEntries(keys.map((k) => [k, (z.share[k as keyof typeof z.share] ?? 0) * 100])),
    } as { start: string; belowAet: number; minutes: number[] } & Record<string, number>;
  });
  return (
    <div role="img" aria-label="Share of time in each zone by week, against an 80% below-AeT target" style={{ height: 200 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="25%">
          <CartesianGrid vertical={false} stroke="var(--line)" />
          <XAxis dataKey="start" tickLine={false} axisLine={{ stroke: "var(--line)" }} tick={{ fontSize: 12, fill: "var(--muted)" }}
            tickFormatter={(d: string) => dayLabel(d, { month: "numeric", day: "numeric" })} />
          <YAxis width={40} domain={[0, 100]} ticks={[0, 50, 100]} tickLine={false} axisLine={false}
            tick={{ fontSize: 12, fill: "var(--muted)" }} tickFormatter={(v: number) => `${v}%`} />
          <Tooltip
            cursor={{ fill: "var(--hover-bg)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as (typeof data)[number];
              const total = d.minutes.reduce((a, b) => a + b, 0);
              return (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>Week of {dayLabel(d.start, { month: "short", day: "numeric" })}</div>
                  {total === 0 ? <div>No heart-rate time</div> : (
                    <>
                      {keys.map((k, i) => (
                        <div key={k}>Zone {i + 1} <span className="mono">{pct(d[k] / 100)}</span>, {Math.round(d.minutes[i])} min</div>
                      ))}
                      <div>Below AeT <span className="mono">{pct(d.belowAet)}</span></div>
                    </>
                  )}
                </div>
              );
            }}
          />
          {keys.map((k, i) => (
            <Bar key={k} dataKey={k} stackId="z" fill={colors[i]} stroke="var(--surface)" strokeWidth={1}
              radius={i === n - 1 ? [4, 4, 0, 0] : undefined} isAnimationActive={false} />
          ))}
          <ReferenceLine y={80} stroke="var(--target)" strokeWidth={2} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
