import Link from "next/link";
import EngineDown from "@/components/EngineDown";
import { engine, EngineError } from "@/lib/api";
import { dayLabel, duration, mi, n0 } from "@/lib/format";
import type { ActivitySummary } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ActivitiesPage() {
  let acts: ActivitySummary[];
  try {
    acts = await engine<ActivitySummary[]>("/api/activities?limit=100");
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }
  return (
    <>
      <h1 className="title">Activities</h1>
      <section className="card">
        {acts.length === 0 ? <div className="muted">No scored activities yet.</div> : (
          <div className="table-wrap">
            <table style={{ minWidth: 720 }}>
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
                {acts.map((a) => (
                  <tr key={a.id}>
                    <td>{dayLabel(a.day, { month: "short", day: "numeric" })}</td>
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
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="muted">Loads have no unit. Cardio 100 is an hour at threshold heart rate; climb and descent 1 is a foot on a moderate grade. * estimated from pace.</div>
      </section>
    </>
  );
}
