"use client";

import { usePlanDetail } from "@/components/plan/PlanDetail";
import { dayLabel, mi, n0 } from "@/lib/format";
import { minutesText } from "@/lib/plan";
import type { PlanSession } from "@/lib/types";

const OFF = new Set(["rest", "done"]);

function when(day: string, today: string): string {
  const [y, m, d] = today.split("-").map(Number);
  const tomorrow = new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
  if (day === today) return "Today";
  if (day === tomorrow) return `Tomorrow, ${dayLabel(day, { weekday: "long" })}`;
  return dayLabel(day, { weekday: "long", month: "short", day: "numeric" });
}

// The next run on the plan, big: distance and vert together, how long, and how to run it.
// Then the next few after it, one line each. Tap any of them for its detail, or to change it.
export default function NextRun({ sessions, today }: { sessions: PlanSession[]; today: string }) {
  const detail = usePlanDetail();
  const open = (s: PlanSession) => detail?.openDay(s);
  const runs = sessions.filter((s) => s.day >= today && !OFF.has(s.kind));
  const next = runs[0];
  if (!next) {
    return (
      <section className="card dark">
        <h2 className="label">Next run</h2>
        <div style={{ fontSize: 22, fontWeight: 600 }}>Nothing in the next 7 days</div>
        <div style={{ color: "var(--feature-soft)" }}>Rest, or add a run on the calendar.</div>
      </section>
    );
  }
  const restToday = next.day !== today && sessions.find((s) => s.day === today)?.kind;
  return (
    <section className="card dark" style={{ gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label">Next run · {when(next.day, today)}</h2>
        {restToday ? <span style={{ color: "var(--feature-muted)", fontSize: 13 }}>Today: {restToday === "done" ? "done" : "rest"}</span> : null}
      </div>
      <div style={{ fontSize: 26, fontWeight: 650, lineHeight: 1.15 }}>
        {next.title}{next.own ? <span style={{ fontSize: 14, fontWeight: 400, color: "var(--feature-muted)" }}> · your run</span> : null}
      </div>
      <div className="mono" style={{ fontSize: 18 }}>
        {mi(next.miles)} mi · {n0(next.gain_ft)} ft up · {n0(next.loss_ft)} ft down
      </div>
      {next.steps && next.steps.length > 1 ? (
        <div style={{ color: "var(--feature-soft)", display: "flex", flexDirection: "column", gap: 4 }}>
          <span>{minutesText(next.minutes)}.</span>
          <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 4 }}>
            {next.steps.map((t, i) => <li key={i}>{t}</li>)}
          </ol>
        </div>
      ) : <div style={{ color: "var(--feature-soft)" }}>{minutesText(next.minutes)}. {next.how}</div>}
      {next.benefit ? (
        <div style={{ color: "var(--feature-soft)", fontSize: 14 }}><span style={{ fontWeight: 600 }}>Why: </span>{next.benefit}</div>
      ) : null}
      {detail ? (
        <div className="actions" style={{ gap: 8 }}>
          <button type="button" className="btn light" style={{ minHeight: 36, padding: "0 14px" }} onClick={() => open(next)}>Change this run</button>
          <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 14px", background: "transparent", color: "var(--on-feature)" }}
            onClick={() => detail.openWeek(sessions[0]?.day ?? today)}>This week, day by day</button>
        </div>
      ) : null}
      {runs.length > 1 ? (
        <div style={{ borderTop: "1px solid var(--feature-line)", paddingTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
          {runs.slice(1, 4).map((s) => (
            <button key={s.day} type="button" onClick={() => open(s)} disabled={!detail}
              style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14, background: "none", border: 0, padding: "2px 0",
                color: "inherit", font: "inherit", textAlign: "left", cursor: detail ? "pointer" : "default" }}>
              <span><span style={{ color: "var(--feature-muted)" }}>{dayLabel(s.day, { weekday: "short" })}</span> {s.title}</span>
              <span className="mono" style={{ fontSize: 13 }}>{mi(s.miles)} mi · {n0(s.gain_ft)} ft</span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}
