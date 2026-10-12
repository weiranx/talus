import Link from "next/link";
import EngineDown from "@/components/EngineDown";
import { engine, engineFailed } from "@/lib/api";
import { dayLabel, duration, mi, n0 } from "@/lib/format";
import type { ActivitySummary } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Monday of the activity's week, as an ISO date. */
function weekOf(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return d.toISOString().slice(0, 10);
}

type Week = { start: string; acts: ActivitySummary[]; miles: number; gain: number; loss: number };

function byWeek(acts: ActivitySummary[]): Week[] {
  const weeks: Week[] = [];
  for (const a of acts) {
    const start = weekOf(a.day);
    let w = weeks.at(-1);
    if (!w || w.start !== start) weeks.push((w = { start, acts: [], miles: 0, gain: 0, loss: 0 }));
    w.acts.push(a);
    w.miles += a.distance_mi;
    w.gain += a.gain_ft ?? 0;
    w.loss += a.loss_ft ?? 0;
  }
  return weeks;
}

const weekTitle = (start: string) => `Week of ${dayLabel(start, { month: "short", day: "numeric" })}`;
const weekTotals = (w: Week) => `${mi(w.miles)} mi · ${n0(w.gain)} ft up · ${n0(w.loss)} ft down`;

export default async function ActivitiesPage() {
  let acts: ActivitySummary[];
  try {
    acts = await engine<ActivitySummary[]>("/api/activities?limit=100");
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }
  const weeks = byWeek(acts);
  return (
    <>
      <header className="page-head">
        <h1 className="title">Activities</h1>
        {acts.length ? <div className="muted" style={{ paddingBottom: 6 }}>Last {acts.length}, grouped by week</div> : null}
      </header>
      <section className="card">
        {acts.length === 0 ? <div className="muted">No scored activities yet.</div> : (
          <>
            <div className="table-wrap act-table">
              <table className="linked" style={{ minWidth: 720 }}>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Activity</th>
                    <th scope="col" className="num">Miles</th>
                    <th scope="col" className="num">Gain, ft</th>
                    <th scope="col" className="num">Descent, ft</th>
                    <th scope="col" className="num">Effort mi</th>
                    <th scope="col" className="num">Time</th>
                    <th scope="col" className="num">Cardio</th>
                    <th scope="col" className="num">Climb</th>
                    <th scope="col" className="num">Descent</th>
                  </tr>
                </thead>
                <tbody>
                  {weeks.map((w) => [
                    <tr key={w.start} className="week-head">
                      <td colSpan={2}><span className="label">{weekTitle(w.start)}</span></td>
                      <td className="num week-sum">{mi(w.miles)}</td>
                      <td className="num week-sum">{n0(w.gain)}</td>
                      <td className="num week-sum">{n0(w.loss)}</td>
                      <td colSpan={5} />
                    </tr>,
                    ...w.acts.map((a) => (
                      <tr key={a.id}>
                        <td>{dayLabel(a.day, { weekday: "short", month: "short", day: "numeric" })}</td>
                        <td><Link href={`/activities/${a.id}`}>{a.name ?? "Activity"}</Link></td>
                        <td className="num">{mi(a.distance_mi)}</td>
                        <td className="num">{n0(a.gain_ft)}</td>
                        <td className="num">{n0(a.loss_ft)}</td>
                        <td className="num">{mi(a.effort_mi)}</td>
                        <td className="num">{duration(a.moving_time_s)}</td>
                        <td className="num">{a.cardio_load == null ? "–" : `${n0(a.cardio_load)}${a.cardio_estimated ? "*" : ""}`}</td>
                        <td className="num">{n0(a.climb_load)}</td>
                        <td className="num">{n0(a.descent_load)}</td>
                      </tr>
                    )),
                  ])}
                </tbody>
              </table>
            </div>

            {/* Phone: one line per run with distance and vert side by side, no sideways scrolling. */}
            <div className="act-list">
              {weeks.map((w) => (
                <div key={w.start} className="act-week">
                  <div className="act-week-head">
                    <span className="label">{weekTitle(w.start)}</span>
                    <span className="mono act-week-sum">{weekTotals(w)}</span>
                  </div>
                  {w.acts.map((a) => (
                    <Link key={a.id} href={`/activities/${a.id}`} className="act-row">
                      <div style={{ minWidth: 0 }}>
                        <div className="act-name">{a.name ?? "Activity"}</div>
                        <div className="muted">{dayLabel(a.day, { weekday: "short", month: "short", day: "numeric" })} · {duration(a.moving_time_s)}</div>
                      </div>
                      <div className="act-nums mono">
                        <span>{mi(a.distance_mi)}<span className="unit">mi</span></span>
                        <span>{n0(a.gain_ft)}<span className="unit">ft up</span></span>
                        <span>{n0(a.loss_ft)}<span className="unit">ft down</span></span>
                      </div>
                    </Link>
                  ))}
                </div>
              ))}
            </div>
          </>
        )}
        <div className="muted">Loads have no unit. Cardio 100 is an hour at threshold heart rate; climb and descent 1 is a foot on a moderate grade. * estimated from pace.</div>
      </section>
    </>
  );
}
