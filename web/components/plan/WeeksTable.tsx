import Link from "next/link";
import { dayLabel, mi, n0 } from "@/lib/format";
import { FOCUS_TEXT, hours } from "@/lib/plan";
import type { PlanWeek } from "@/lib/types";

// Week targets to the objective: miles and feet side by side, the long day, and why a week was held back.
export default function WeeksTable({ weeks, current, limit, full }: {
  weeks: PlanWeek[]; current: string; limit?: number; full?: boolean;
}) {
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
            <th scope="col" className="num">Long day</th>
            {full ? <th scope="col">Notes</th> : null}
          </tr>
        </thead>
        <tbody>
          {shown.map((w) => (
            <tr key={w.start} className={w.start === current ? "hl" : undefined}>
              <td>{dayLabel(w.start, { month: "short", day: "numeric" })}</td>
              <td>{FOCUS_TEXT[w.focus]}{w.limited_by.length ? " *" : ""}</td>
              <td className="num">{mi(w.miles)}</td>
              <td className="num">{n0(w.gain_ft)}</td>
              <td className="num">{n0(w.loss_ft)}</td>
              <td className="num">{hours(w.hours)}</td>
              <td className="num">{w.long ? `${mi(w.long.miles)} mi, ${n0(w.long.gain_ft)} ft` : "–"}</td>
              {full ? <td style={{ whiteSpace: "normal", minWidth: 220, fontSize: 13 }} className="muted">{w.notes.join(". ")}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
      {weeks.some((w) => w.limited_by.length) ? (
        <div className="muted" style={{ marginTop: 8 }}>* Held back so a load doesn&apos;t ramp too fast.</div>
      ) : null}
      {limit && weeks.length > from + limit ? (
        <div style={{ marginTop: 8 }}><Link href="/plan/weeks">All {weeks.length} weeks to the objective</Link></div>
      ) : null}
    </div>
  );
}
