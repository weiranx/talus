import Link from "next/link";
import { dayLabel, duration, mi, n0 } from "@/lib/format";
import type { TodayAhead } from "@/lib/types";

const BARS = [
  { key: "miles", label: "Miles", ledger: "distance", fmt: mi },
  { key: "gain_ft", label: "Ft up", ledger: "climb", fmt: n0 },
  { key: "loss_ft", label: "Ft down", ledger: "descent", fmt: n0 },
] as const;

const pct = (v: number, top: number) => `${Math.min(100, (v / top) * 100)}%`;

/** Today's place in the bigger picture: days to the objective, this week against its target,
 *  and the next few days on the calendar. */
export default function ComingUp({ ahead }: { ahead: TodayAhead }) {
  const { objective: o, week: w, days } = ahead;
  const anything = days.some((d) => d.runs.length);
  if (!ahead.has_plan && !anything && !w) {
    return (
      <section className="card" style={{ gap: 6 }}>
        <h2 className="label">Coming up</h2>
        <div className="muted" style={{ fontSize: 14 }}>
          Nothing scheduled in the next {days.length} days. <Link href="/plan">Plan your week</Link> and the coach reads today against it.
        </div>
      </section>
    );
  }
  const target = w?.target ?? null;
  return (
    <section className="card" style={{ gap: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h2 className="label">Coming up</h2>
        <Link href="/plan">{ahead.has_plan ? "Plan" : "Calendar"}</Link>
      </div>

      {o ? (
        <div className="cu-goal">
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>{o.name}</div>
            <div className="muted">
              {o.event ? o.goal_text : `${o.goal_text}, to`} {dayLabel(o.day, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}
            </div>
          </div>
          {o.event && o.days_to_go >= 0 ? (
            <div style={{ textAlign: "right" }}>
              <span className="cu-days">{o.days_to_go}</span> <span className="muted">{o.days_to_go === 1 ? "day" : "days"} to go</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {w && target && w.done ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="muted">
            This week{target.focus ? <>, a <span style={{ color: "var(--ink)" }}>{target.focus}</span> week</> : null}, against its target. The lighter part is still scheduled.
          </div>
          <div className="cu-bars">
            {BARS.map((b) => {
              const done = w.done![b.key], sched = w.ahead[b.key], goal = target[b.key];
              const top = Math.max(goal, done + sched) || 1;
              return (
                <div key={b.key} className="cu-bar" style={{ ["--c" as string]: `var(--${b.ledger})` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 6, fontSize: 13 }}>
                    <span>{b.label}</span>
                    <span className="mono"><b>{b.fmt(done)}</b> <span className="muted">of {b.fmt(goal)}</span></span>
                  </div>
                  <div className="cu-meter" role="img" aria-label={`${b.label}: ${b.fmt(done)} done, ${b.fmt(sched)} still scheduled, target ${b.fmt(goal)}`}>
                    <span className="done" style={{ width: pct(done, top) }} />
                    <span className="sched" style={{ left: pct(done, top), width: pct(sched, top) }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : w && w.ahead.miles > 0 ? (
        <div className="muted">
          Still scheduled this week: <span className="mono" style={{ color: "var(--ink)" }}>{mi(w.ahead.miles)} mi, {n0(w.ahead.gain_ft)} ft up</span>
        </div>
      ) : null}

      <ul className="cu-list">
        {days.map((d) => (
          <li key={d.day}>
            <span className="cu-dow">{dayLabel(d.day)}</span>
            {d.runs.length ? (
              <>
                <span style={{ minWidth: 0 }}>{d.runs.map((r) => r.title).join(", ")}</span>
                <span className="cu-size">
                  {d.runs.map((r) => `${mi(r.miles)} mi, ${n0(r.gain_ft)} ft up${r.minutes ? `, ${duration(r.minutes * 60)}` : ""}`).join("; ")}
                </span>
              </>
            ) : (
              <span className="muted" style={{ gridColumn: "2 / -1" }}>{ahead.has_plan ? "Rest" : "Nothing scheduled"}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
