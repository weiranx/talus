"use client";

import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dayLabel, pct } from "@/lib/format";
import type { LoadData } from "@/lib/types";

export default function IntensityChart({ weeks }: { weeks: LoadData["intensity_by_week"] }) {
  const data = weeks.map((w) => ({ start: w.start, z1: w.share.z1 * 100, z2: w.share.z2 * 100, z3: w.share.z3 * 100, minutes: w.minutes }));
  return (
    <div role="img" aria-label="Share of time in each zone by week, against an 80% Zone 1 target" style={{ height: 200 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="25%">
          <CartesianGrid vertical={false} stroke="#e3e8e5" />
          <XAxis dataKey="start" tickLine={false} axisLine={{ stroke: "#e3e8e5" }} tick={{ fontSize: 12, fill: "#52605a" }}
            tickFormatter={(d: string) => dayLabel(d, { month: "numeric", day: "numeric" })} />
          <YAxis width={40} domain={[0, 100]} ticks={[0, 50, 100]} tickLine={false} axisLine={false}
            tick={{ fontSize: 12, fill: "#52605a" }} tickFormatter={(v: number) => `${v}%`} />
          <Tooltip
            cursor={{ fill: "rgba(21,33,29,0.04)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as (typeof data)[number];
              const total = d.minutes.z1 + d.minutes.z2 + d.minutes.z3;
              return (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>Week of {dayLabel(d.start, { month: "short", day: "numeric" })}</div>
                  {total === 0 ? <div>No heart-rate time</div> : (["z1", "z2", "z3"] as const).map((z, i) => (
                    <div key={z}>Zone {i + 1} <span className="mono">{pct(d[z] / 100)}</span>, {Math.round(d.minutes[z])} min</div>
                  ))}
                </div>
              );
            }}
          />
          <Bar dataKey="z1" stackId="z" fill="var(--z1)" stroke="#fff" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="z2" stackId="z" fill="var(--z2)" stroke="#fff" strokeWidth={1} isAnimationActive={false} />
          <Bar dataKey="z3" stackId="z" fill="var(--z3)" stroke="#fff" strokeWidth={1} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          <ReferenceLine y={80} stroke="var(--target)" strokeWidth={2} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
