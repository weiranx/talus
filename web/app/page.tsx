import Link from "next/link";
import { Suspense } from "react";
import CallCard from "@/components/CallCard";
import CoachCall from "@/components/CoachCall";
import CheckIn from "@/components/CheckIn";
import EngineDown from "@/components/EngineDown";
import Explain from "@/components/Explain";
import Overnight from "@/components/Overnight";
import Pill, { statePill } from "@/components/Pill";
import ReadinessScore from "@/components/ReadinessScore";
import Sparkline from "@/components/Sparkline";
import ZoneBar from "@/components/ZoneBar";
import { engine, EngineError } from "@/lib/api";
import { dayLabel, LEDGER_LABEL, ledgerBadge, ledgerDecimals, ledgerNum, ledgerUnit, mi, n0, stateLabel } from "@/lib/format";
import { LEDGERS, type Today } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TodayPage() {
  let t: Today;
  try {
    t = await engine<Today>("/api/today");
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }

  return (
    <>
      <header className="page-head">
        <div>
          <div className="eyebrow">{dayLabel(t.date, { weekday: "long", month: "short", day: "numeric" })}</div>
          <h1 className="title">Today</h1>
        </div>
        <div className="muted" style={{ paddingBottom: 6 }}>
          {t.checkin ? "Check-in done." : "Check in to sharpen the legs reading."}
        </div>
      </header>

      <div className="row">
        <div style={{ flex: "2 1 420px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
        <Suspense fallback={<CallCard call={t.call} planned={t.call.planned} note="Rule-based call. The AI coach is writing today's brief." pending />}>
          <CoachCall rules={t.call} />
        </Suspense>
        {t.planned && !t.call.planned && t.planned.kind !== "rest" ? (
          <section className="card" style={{ gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
              <h2 className="label">Scheduled today{t.planned.source === "plan" ? ", from your plan" : ""}</h2>
              <Link href="/plan">Calendar</Link>
            </div>
            <div style={{ fontWeight: 600 }}>{t.planned.title}</div>
            <div className="muted">{t.planned.detail}</div>
            <div>
              {t.call.call === "rest"
                ? "Today's call is rest, so skip it or move it to another day."
                : "Today's call eases it: follow the call above, not the full run."}
            </div>
          </section>
        ) : null}
        {t.checkin ? null : <CheckIn initial={null} />}
        </div>

        <div style={{ flex: "1 1 300px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16, alignSelf: "flex-start" }}>
        <section className="card" style={{ gap: 14 }}>
          <h2 className="label">Readiness</h2>
          <ReadinessScore s={t.score} />
          <div className="divider" />
          {(["engine", "legs"] as const).map((k) => (
            <div key={k} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 17, fontWeight: 600 }}>{k === "engine" ? "Engine" : "Legs"}</div>
                  <div className="muted">{t.readiness[k].detail}</div>
                </div>
                <Pill tone={statePill(t.readiness[k].state)}>{stateLabel(t.readiness[k].state)}</Pill>
              </div>
            </div>
          ))}
        </section>
        <Overnight w={t.wellness} />
        </div>
      </div>

      <div className="row">
        {LEDGERS.map((name) => {
          const d = t.ledgers[name];
          if (!d) return null;
          const badge = ledgerBadge(d);
          const limiter = t.readiness.legs.limiter === name && t.readiness.legs.state !== "fresh";
          const nd = ledgerDecimals(name), unit = ledgerUnit(name);
          return (
            <section key={name} className="card" style={{ flex: "1 1 220px", gap: 10, outline: limiter ? `2px solid var(--${name})` : undefined }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="swatch" style={{ background: `var(--${name})` }} />
                  <h2 style={{ fontSize: 17, fontWeight: 600 }}>{LEDGER_LABEL[name]}</h2>
                </div>
                <Pill tone={badge.tone}>{badge.text}</Pill>
              </div>
              <div>
                <div className="big">
                  <Explain path={`ledger/${name}/vs_normal`}>{d.vs_normal_text.replace(/^./, (c) => c.toUpperCase())}</Explain>
                </div>
                <div className="muted">
                  Last 7 days <span className="mono" style={{ color: "var(--ink)" }}><Explain path={`ledger/${name}/fatigue`}>{ledgerNum(d.fatigue, nd) + unit}</Explain></span> a day. Your normal{" "}
                  <span className="mono" style={{ color: "var(--ink)" }}><Explain path={`ledger/${name}/fitness`}>{ledgerNum(d.fitness, nd) + unit}</Explain></span>.
                </div>
              </div>
              <Sparkline data={d.spark} color={`var(--${name})`} label={LEDGER_LABEL[name]} decimals={nd} unit={unit} />
            </section>
          );
        })}
      </div>

      <div className="row">
        <section className="card" style={{ flex: "2 1 480px" }}>
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
            <h2 className="label">This week, from {dayLabel(t.week.start, { month: "short", day: "numeric" })}</h2>
            <div className="mono" style={{ fontSize: 13 }}>
              {mi(t.week.distance_mi)} mi, {n0(t.week.gain_ft)} ft up, {n0(t.week.loss_ft)} ft down
            </div>
          </div>
          {t.week.activities.length === 0 ? (
            <div className="muted">No activities yet this week.</div>
          ) : (
            <div className="table-wrap">
              <table style={{ minWidth: 440 }}>
                <thead>
                  <tr>
                    <th scope="col">Day</th>
                    <th scope="col">Activity</th>
                    <th scope="col" className="num">Miles</th>
                    <th scope="col" className="num">Gain, ft</th>
                    <th scope="col" className="num">Descent, ft</th>
                  </tr>
                </thead>
                <tbody>
                  {t.week.activities.map((a) => (
                    <tr key={a.id}>
                      <td>{dayLabel(a.day)}</td>
                      <td><Link href={`/activities/${a.id}`}>{a.name ?? "Activity"}</Link></td>
                      <td className="num">{mi(a.distance_mi)}</td>
                      <td className="num">{n0(a.gain_ft)}</td>
                      <td className="num">{n0(a.loss_ft)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div style={{ flex: "1 1 300px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
              <h2 className="label">Intensity, 28 days</h2>
              <div className="muted" style={{ fontSize: 12 }}>Target 80% below AeT</div>
            </div>
            <ZoneBar share={t.zones_28d?.share ?? { ...t.intensity_28d.share }} model={t.zone_model}
              belowAet={t.intensity_28d.share.z1} />
          </section>
          {t.checkin ? <CheckIn initial={t.checkin} /> : null}
        </div>
      </div>
    </>
  );
}
