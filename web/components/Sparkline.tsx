"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { dayLabel, ledgerNum } from "@/lib/format";

type Point = { day: string; fitness: number; fatigue: number };

export default function Sparkline({ data, color, label, decimals = 0, unit = "" }: {
  data: Point[]; color: string; label: string; decimals?: number; unit?: string;
}) {
  const num = (v: number) => ledgerNum(v, decimals) + unit;
  return (
    <div role="img" aria-label={`${label}: your normal and your last 7 days over 6 weeks`} style={{ height: 56 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 2, bottom: 4, left: 2 }}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Tooltip
            cursor={{ stroke: "#c9d2ce" }}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>{dayLabel(String(payload[0].payload.day), { month: "short", day: "numeric" })}</div>
                  <div>Your normal <span className="mono">{num(payload[0].payload.fitness)}</span></div>
                  <div>Last 7 days <span className="mono">{num(payload[0].payload.fatigue)}</span></div>
                </div>
              ) : null
            }
          />
          <Line dataKey="fatigue" stroke={color} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          <Line dataKey="fitness" stroke={color} strokeWidth={2.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
