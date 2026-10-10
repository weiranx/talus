"use client";

import { Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { dayLabel } from "@/lib/format";
import type { ScorePoint } from "@/lib/types";

const COLOR = { go: "var(--good-ink)", modify: "#c98a2b", rest: "var(--bad-ink)" } as const;

/** The readiness score for each of the last 28 days, colored by that day's call. */
export default function ScoreTrend({ data }: { data: ScorePoint[] }) {
  if (data.length < 2) return null;
  return (
    <div role="img" aria-label={`Readiness score over the last ${data.length} days`} style={{ height: 64 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }} barCategoryGap={2}>
          <YAxis hide domain={[0, 100]} />
          <ReferenceLine y={67} stroke="#c9d2ce" strokeDasharray="3 3" />
          <Tooltip
            cursor={{ fill: "rgba(0,0,0,0.05)" }}
            content={({ active, payload }) =>
              active && payload?.length ? (
                <div className="tip">
                  <div style={{ fontWeight: 600 }}>{dayLabel(String(payload[0].payload.day), { weekday: "short", month: "short", day: "numeric" })}</div>
                  <div><span className="mono">{payload[0].payload.score}</span> {payload[0].payload.label}</div>
                </div>
              ) : null
            }
          />
          <Bar dataKey="score" minPointSize={3} isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {data.map((d) => <Cell key={d.day} fill={COLOR[d.call]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
