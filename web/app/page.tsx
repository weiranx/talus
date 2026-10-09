import Link from "next/link";
import CheckIn from "@/components/CheckIn";
import EngineDown from "@/components/EngineDown";
import Pill, { statePill } from "@/components/Pill";
import Sparkline from "@/components/Sparkline";
import ZoneBar from "@/components/ZoneBar";
import { engine, EngineError } from "@/lib/api";
import { dayLabel, LEDGER_LABEL, ledgerBadge, mi, n0, stateLabel } from "@/lib/format";
import { LEDGERS, type Today } from "@/lib/types";

export const dynamic = "force-dynamic";

const CALL_TONE = { go: "good", modify: "warn", rest: "bad" } as const;
const CALL_TEXT = { go: "Go", modify: "Modify", rest: "Rest" } as const;

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
        <section className="card dark" style={{ padding: "22px 24px 18px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 className="label">Today&apos;s call</h2>
            <Pill tone={CALL_TONE[t.call.call]}>{CALL_TEXT[t.call.call]}</Pill>
          </div>
          <div style={{ fontSize: 28, lineHeight: "33px", fontWeight: 700, letterSpacing: "-0.01em" }}>{t.call.session}</div>
          <div style={{ color: "#e4eae7", maxWidth: 620 }}>{t.call.detail}</div>
          <div style={{ color: "#b9c6c0", fontSize: 14, maxWidth: 620 }}>Why: {t.call.why}</div>
          <div style={{ color: "#b9c6c0", fontSize: 12 }}>Rule-based call from your load data. The AI coach replaces it in a later milestone.</div>
        </section>
        {t.checkin ? null : <CheckIn initial={null} />}
        </div>

        <section className="card" style={{ flex: "1 1 300px", gap: 14, alignSelf: "flex-start" }}>
          <h2 className="label">Readiness</h2>
          {(["engine", "legs"] as const).map((k, i) => (
            <div key={k} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {i > 0 ? <div className="divider" /> : null}
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
      </div>

      <div className="row">
        {LEDGERS.map((name) => {
          const d = t.ledgers[name];
          if (!d) return null;
          const badge = ledgerBadge(d);
          const limiter = t.readiness.legs.limiter === name && t.readiness.legs.state !== "fresh";
          return (
            <section key={name} className="card" style={{ flex: "1 1 260px", gap: 10, outline: limiter ? `2px solid var(--${name})` : undefined }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="swatch" style={{ background: `var(--${name})` }} />
                  <h2 style={{ fontSize: 17, fontWeight: 600 }}>{LEDGER_LABEL[name]}</h2>
                </div>
                <Pill tone={badge.tone}>{badge.text}</Pill>
              </div>
              <div>
                <div className="big">{d.vs_normal_text.replace(/^./, (c) => c.toUpperCase())}</div>
                <div className="muted">
                  Last 7 days <span className="mono" style={{ color: "var(--ink)" }}>{n0(d.fatigue)}</span> a day. Your normal{" "}
                  <span className="mono" style={{ color: "var(--ink)" }}>{n0(d.fitness)}</span>.
                </div>
              </div>
              <Sparkline data={d.spark} color={`var(--${name})`} label={LEDGER_LABEL[name]} />
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
              <div className="muted" style={{ fontSize: 12 }}>Target 80% Zone 1</div>
            </div>
            <ZoneBar share={t.intensity_28d.share} />
          </section>
          {t.checkin ? <CheckIn initial={t.checkin} /> : null}
        </div>
      </div>
    </>
  );
}
