import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import CoachRead, { CoachReadPending } from "@/components/CoachRead";
import ActivityView from "@/components/ActivityView";
import EngineDown from "@/components/EngineDown";
import Explain from "@/components/Explain";
import { engine, engineFailed } from "@/lib/api";
import { dayLabel, duration, LEDGER_LABEL, mi, n0, pct } from "@/lib/format";
import { LEDGERS, type ActivityDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

const BANDS: [string, string][] = [["<8", "Under 8%"], ["8-20", "8 to 20%"], [">20", "Over 20%"]];

export default async function ActivityPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let a: ActivityDetail;
  try {
    a = await engine<ActivityDetail>(`/api/activities/${encodeURIComponent(id)}`);
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }
  const stats: [string, ReactNode][] = [
    ["Distance", `${mi(a.distance_mi)} mi`],
    ["Gain", `${n0(a.gain_ft)} ft`],
    ["Descent", `${n0(a.loss_ft)} ft`],
    ["Time", duration(a.moving_time_s, true)],
    ["Effort miles", <Explain key="e" path={`activity/${a.id}/effort_mi`}>{mi(a.effort_mi)}</Explain>],
    ["Hike share", pct(a.hike_share)],
  ];

  return (
    <>
      <header className="page-head">
        <div>
          <div className="eyebrow">
            <Link href="/activities">Activities</Link> / {dayLabel(a.day, { weekday: "long", month: "short", day: "numeric" })}
          </div>
          <h1 className="title">{a.name ?? "Activity"}</h1>
          <div className="muted">
            {({ intervals: "Synced from intervals.icu", garmin: "Synced from Garmin", demo: "Demo data" } as Record<string, string>)[a.source] ?? "Imported file"}
            {a.device_name ? `, recorded on ${a.device_name}` : ""}.
          </div>
        </div>
      </header>

      <section className="card">
        <div className="stat-grid">
          {stats.map(([k, v]) => (
            <div key={k} className="stat">
              <div className="muted">{k}</div>
              <div className="v">{v}</div>
            </div>
          ))}
        </div>
        <div className="divider" />
        <div className="stat-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          {LEDGERS.map((name) => {
            const l = a.loads[name];
            return (
              <div key={name} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                <span className="swatch" style={{ background: `var(--${name})`, marginTop: 5 }} />
                <div>
                  <div style={{ fontWeight: 600 }}>{LEDGER_LABEL[name]}</div>
                  <div className="big mono">
                    {l.times_usual_day == null ? "n/a" : <Explain path={`activity/${a.id}/times_usual_${name}`}>{`${l.times_usual_day.toFixed(1)}x`}</Explain>}
                  </div>
                  <div className="muted">
                    {l.load == null ? "not scored, no heart rate" : (
                      <>{l.times_usual_day == null ? "" : "a usual day, "}{l.six_week_high ? "6-week high, " : ""}{name === "distance" ? `${mi(l.load)} mi` : <>load <Explain path={`activity/${a.id}/${name}_load`}>{n0(l.load)}</Explain></>}{name === "cardio" && a.cardio_estimated ? ", estimated" : ""}</>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        {a.notes.length ? <div className="muted">{a.notes.join(" ")}</div> : null}
      </section>

      <Suspense fallback={<CoachReadPending />}>
        <CoachRead id={id} />
      </Suspense>

      <ActivityView a={a} />

      <section className="card">
        <h2 className="label">Vert by grade</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th scope="col">Grade</th><th scope="col" className="num">Gain, ft</th><th scope="col" className="num">Descent, ft</th></tr>
            </thead>
            <tbody>
              {BANDS.map(([k, label]) => (
                <tr key={k}>
                  <td>{label}</td>
                  <td className="num">{n0(a.gain_by_band_ft[k])}</td>
                  <td className="num">{n0(a.loss_by_band_ft[k])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="muted">
          Longest climb {n0(a.longest_climb_ft)} ft. Longest descent {n0(a.longest_descent_ft)} ft.
          {a.minutes_above_8000ft >= 1 ? ` ${Math.round(a.minutes_above_8000ft)} min above 8,000 ft.` : ""}
        </div>
      </section>
    </>
  );
}
