"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dayLabel, n0 } from "@/lib/format";
import type { LedgerDay } from "@/lib/types";

export default function LedgerChart({ series, color, label }: { series: LedgerDay[]; color: string; label: string }) {
  const long = series.length > 70;
  return (
    <div role="img" aria-label={`${label} load: your normal (solid) and last 7 days (dashed)`} style={{ height: 180 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="#e3e8e5" />
          <XAxis dataKey="day" tickLine={false} axisLine={{ stroke: "#e3e8e5" }} minTickGap={32}
            tick={{ fontSize: 12, fill: "#52605a" }}
            tickFormatter={(d: string) => dayLabel(d, long ? { month: "short" } : { month: "numeric", day: "numeric" })} />
          <YAxis width={48} tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: "#52605a" }}
            tickFormatter={(v: number) => n0(v)} />
          <Tooltip
            cursor={{ stroke: "#c9d2ce" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as LedgerDay;
              return (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>{dayLabel(d.day, { weekday: "short", month: "short", day: "numeric" })}</div>
                  <div>Load that day <span className="mono">{n0(d.load)}</span></div>
                  <div>Your normal <span className="mono">{n0(d.fitness)}</span></div>
                  <div>Last 7 days <span className="mono">{n0(d.fatigue)}</span></div>
                  <div>{d.vs_normal_text}</div>
                  {d.flags.map((f) => <div key={f.key} style={{ color: "var(--warn-ink)", fontWeight: 600 }}>{f.text}</div>)}
                </div>
              );
            }}
          />
          <Line dataKey="fatigue" stroke={color} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          <Line dataKey="fitness" stroke={color} strokeWidth={2.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
