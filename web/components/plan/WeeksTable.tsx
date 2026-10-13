import Link from "next/link";
import { dayLabel, mi, n0 } from "@/lib/format";
import { FOCUS_TEXT, HILL_TEXT, hours, WORKOUT_TEXT } from "@/lib/plan";
import type { LongDay, PlanWeek, WeekActual } from "@/lib/types";

// Week targets to the objective: miles and feet side by side, the long day, and why a week was held back.
// Done: what you actually ran in weeks that have started, colored against the target the way
// the weekly review reads it (under 70% on two of miles, gain and descent is a missed week).
function doneTone(w: PlanWeek, a: WeekActual, current: boolean): string | undefined {
  const shares = (["miles", "gain_ft", "loss_ft"] as const).map((k) => (w[k] > 0 ? a[k] / w[k] : 1));
  if (shares.filter((v) => v >= 0.9).length >= 2) return "var(--good-ink)";
  if (!current && shares.filter((v) => v < 0.7).length >= 2) return "var(--bad-ink)";
  return undefined;
}

// One long day, or a back-to-back pair as "20.1 + 13.4 mi, 5,240 + 3,490 ft".
function longText(l: LongDay): string {
  if (!l.second) return `${mi(l.miles)} mi, ${n0(l.gain_ft)} ft`;
  return `${mi(l.miles)} + ${mi(l.second.miles)} mi, ${n0(l.gain_ft)} + ${n0(l.second.gain_ft)} ft`;
}

export default function WeeksTable({ weeks, current, limit, full, actuals }: {
  weeks: PlanWeek[]; current: string; limit?: number; full?: boolean; actuals?: Record<string, WeekActual>;
}) {
  const showDone = !!actuals && weeks.some((w) => actuals[w.start]);
  const from = Math.max(0, weeks.findIndex((w) => w.start === current));
  const shown = limit ? weeks.slice(from, from + limit) : weeks;
  return (
    <div className="table-wrap">
      <table style={{ minWidth: 640 }}>
        <thead>
          <tr>
            <th scope="col">Week of</th>
            <th scope="col">Focus</th>
            <th scope="col" className="num">Miles</th>
            <th scope="col" className="num">Gain, ft</th>
            <th scope="col" className="num">Descent, ft</th>
            <th scope="col" className="num">Time</th>
            <th scope="col" className="num">{weeks.some((w) => w.long?.second) ? "Long days" : "Long day"}</th>
            {showDone ? <th scope="col" className="num" style={{ paddingLeft: 16 }}>Done</th> : null}
            {full ? <th scope="col">Notes</th> : null}
          </tr>
        </thead>
        <tbody>
          {shown.map((w) => (
            <tr key={w.start} className={w.start === current ? "hl" : undefined}>
              <td>{dayLabel(w.start, { month: "short", day: "numeric" })}</td>
              <td>{FOCUS_TEXT[w.focus]}{w.limited_by.length ? " *" : ""}{w.workout ? <span className="muted"> · {WORKOUT_TEXT[w.workout].toLowerCase()}</span> : null}{w.hill_session ? <span className="muted"> · {HILL_TEXT[w.hill_session].toLowerCase()}</span> : null}</td>
              <td className="num">{mi(w.miles)}</td>
              <td className="num">{n0(w.gain_ft)}</td>
              <td className="num">{n0(w.loss_ft)}</td>
              <td className="num">{hours(w.hours)}</td>
              <td className="num">{w.long ? longText(w.long) : "–"}</td>
              {showDone ? (
                <td className="num" style={{ paddingLeft: 16, color: actuals?.[w.start] ? doneTone(w, actuals[w.start], w.start === current) : undefined }}>
                  {actuals?.[w.start] ? `${mi(actuals[w.start].miles)} mi, ${n0(actuals[w.start].gain_ft)} ft${w.start === current ? " so far" : ""}` : ""}
                </td>
              ) : null}
              {full ? <td style={{ whiteSpace: "normal", minWidth: 220, fontSize: 13 }} className="muted">{[w.coach_note, ...w.notes].filter(Boolean).join(". ")}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
      {weeks.some((w) => w.limited_by.length) ? (
        <div className="muted" style={{ marginTop: 8 }}>* Held back so a load doesn&apos;t ramp too fast.</div>
      ) : null}
      {limit && weeks.length > from + limit ? (
        <div style={{ marginTop: 8 }}><Link href="/plan/weeks">All {weeks.length} weeks</Link></div>
      ) : null}
    </div>
  );
}
