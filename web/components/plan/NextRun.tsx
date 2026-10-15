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
// Then the next few after it, one line each.
export default function NextRun({ sessions, today }: { sessions: PlanSession[]; today: string }) {
  const runs = sessions.filter((s) => s.day >= today && !OFF.has(s.kind));
  const next = runs[0];
  if (!next) {
    return (
      <section className="card" style={{ background: "var(--ink)", color: "#fff" }}>
        <h2 className="label" style={{ color: "#b9c6c0" }}>Next run</h2>
        <div style={{ fontSize: 22, fontWeight: 600 }}>Nothing in the next 7 days</div>
        <div style={{ color: "#d5ddd9" }}>Rest, or add a run on the calendar.</div>
      </section>
    );
  }
  const restToday = next.day !== today && sessions.find((s) => s.day === today)?.kind;
  return (
    <section className="card" style={{ background: "var(--ink)", color: "#fff", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label" style={{ color: "#b9c6c0" }}>Next run · {when(next.day, today)}</h2>
        {restToday ? <span style={{ color: "#b9c6c0", fontSize: 13 }}>Today: {restToday === "done" ? "done" : "rest"}</span> : null}
      </div>
      <div style={{ fontSize: 26, fontWeight: 650, lineHeight: 1.15 }}>
        {next.title}{next.own ? <span style={{ fontSize: 14, fontWeight: 400, color: "#b9c6c0" }}> · your run</span> : null}
      </div>
      <div className="mono" style={{ fontSize: 18 }}>
        {mi(next.miles)} mi · {n0(next.gain_ft)} ft up · {n0(next.loss_ft)} ft down
      </div>
      {next.steps && next.steps.length > 1 ? (
        <div style={{ color: "#d5ddd9", display: "flex", flexDirection: "column", gap: 4 }}>
          <span>{minutesText(next.minutes)}.</span>
          <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 4 }}>
            {next.steps.map((t, i) => <li key={i}>{t}</li>)}
          </ol>
        </div>
      ) : <div style={{ color: "#d5ddd9" }}>{minutesText(next.minutes)}. {next.how}</div>}
      {next.benefit ? (
        <div style={{ color: "#d5ddd9", fontSize: 14 }}><span style={{ color: "#fff", fontWeight: 600 }}>Why: </span>{next.benefit}</div>
      ) : null}
      {runs.length > 1 ? (
        <div style={{ borderTop: "1px solid #34433d", paddingTop: 10, display: "flex", flexDirection: "column", gap: 4 }}>
          {runs.slice(1, 4).map((s) => (
            <div key={s.day} style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 14 }}>
              <span><span style={{ color: "#b9c6c0" }}>{dayLabel(s.day, { weekday: "short" })}</span> {s.title}</span>
              <span className="mono" style={{ fontSize: 13 }}>{mi(s.miles)} mi · {n0(s.gain_ft)} ft</span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
