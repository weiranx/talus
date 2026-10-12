"use client";

import { useId } from "react";
import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { dayLabel, ledgerNum } from "@/lib/format";

type Point = { day: string; fitness: number; fatigue: number };

export default function Sparkline({ data, color, label, decimals = 0, unit = "" }: {
  data: Point[]; color: string; label: string; decimals?: number; unit?: string;
}) {
  const num = (v: number) => ledgerNum(v, decimals) + unit;
  const fill = `spark-${useId().replace(/:/g, "")}`;
  return (
    <div role="img" aria-label={`${label}: your normal and your last 7 days over 6 weeks`} style={{ height: 64 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
          <defs>
            <linearGradient id={fill} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" style={{ stopColor: color, stopOpacity: 0.22 }} />
              <stop offset="1" style={{ stopColor: color, stopOpacity: 0 }} />
            </linearGradient>
          </defs>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Tooltip
            cursor={{ stroke: "var(--line-strong)" }}
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
          <Line dataKey="fatigue" stroke={color} strokeWidth={1.5} strokeOpacity={0.7} strokeDasharray="3 3" dot={false} isAnimationActive={false} />
          <Area dataKey="fitness" stroke={color} strokeWidth={2.5} fill={`url(#${fill})`} baseValue="dataMin" dot={false} activeDot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
