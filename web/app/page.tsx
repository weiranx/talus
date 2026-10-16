import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";
import CallCard from "@/components/CallCard";
import CoachCall from "@/components/CoachCall";
import CheckIn from "@/components/CheckIn";
import ComingUp from "@/components/ComingUp";
import EngineDown from "@/components/EngineDown";
import Overnight from "@/components/Overnight";
import ReadinessScore from "@/components/ReadinessScore";
import RefreshToday from "@/components/RefreshToday";
import TodayLoad from "@/components/TodayLoad";
import ZoneBar from "@/components/ZoneBar";
import SetupList from "@/components/onboarding/SetupList";
import { engine, engineFailed, EngineError } from "@/lib/api";
import { dayLabel, duration, mi, n0, stateLabel } from "@/lib/format";
import { weekDays } from "@/lib/today";
import type { Onboarding, Today } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TodayPage() {
  const [obRes, tRes] = await Promise.allSettled([engine<Onboarding>("/api/onboarding"), engine<Today>("/api/today")]);
  if (obRes.status === "rejected") return <EngineDown message={engineFailed(obRes.reason)} />;
  const ob = obRes.value;
  if (ob.needed) redirect("/welcome");
  if (tRes.status === "rejected") {
    if (tRes.reason instanceof EngineError && tRes.reason.status === 409) return <SetupList ob={ob} empty />;
    return <EngineDown message={engineFailed(tRes.reason)} />;
  }
  const t = tRes.value;
  const days = weekDays(t.week.start, t.date, t.week.activities.map((a) => a.day));
  const lw = t.week.last_week_to_date;
  const upcoming = t.ahead?.days.flatMap((d) => d.runs.map((run, index) => ({ ...run, day: d.day, key: d.day + "-" + index }))).slice(0, 3) ?? [];
  const sleep = t.wellness.sleep.last_night_h;

  return (
    <div className="today-workspace">
      <div className="today-primary">
        <header className="today-header">
          <div>
            <p className="today-date">{dayLabel(t.date, { weekday: "long", month: "long", day: "numeric" })}</p>
            <h1>Today</h1>
          </div>
          <RefreshToday />
        </header>
        <SetupList ob={ob} />
        <Suspense fallback={<CallCard call={t.call} planned={t.call.planned} scheduled={t.planned}
          note="Rule-based call. The AI coach is writing today's brief." pending />}>
          <CoachCall rules={t.call} scheduled={t.planned} />
        </Suspense>
        <TodayLoad today={t} />
        <section className="today-week" aria-labelledby="today-week-title">
          <div className="today-section-head">
            <h2 id="today-week-title">This week</h2>
            <Link href="/activities">View activities <span aria-hidden>›</span></Link>
          </div>
          <dl className="today-week-totals">
            <div><dt>Distance</dt><dd>{mi(t.week.distance_mi)} <span>mi</span></dd></div>
            <div><dt>Ascent</dt><dd>{n0(t.week.gain_ft)} <span>ft</span></dd></div>
            <div><dt>Descent</dt><dd>{n0(t.week.loss_ft)} <span>ft</span></dd></div>
          </dl>
          <ol className="today-week-days" aria-label="Activity this week">
            {days.map((d) => (
              <li key={d.day} data-completed={d.completed} data-future={d.future} aria-current={d.today ? "date" : undefined}>
                <span aria-hidden>{dayLabel(d.day, { weekday: "narrow" })}</span>
                <span className="today-day-mark" aria-hidden />
                <span className="sr-only">{dayLabel(d.day, { weekday: "long", month: "short", day: "numeric" })}: {d.completed ? "activity recorded" : d.future ? "upcoming" : "no activity recorded"}{d.today ? ", today" : ""}</span>
              </li>
            ))}
          </ol>
          <details className="today-disclosure">
            <summary>Week details</summary>
            {lw && lw.activities > 0 ? <p className="today-caption">
              By this day last week: {mi(lw.distance_mi)} mi, {n0(lw.gain_ft)} ft up, {n0(lw.loss_ft)} ft down
              {" "}({weekDelta(t.week.distance_mi, lw.distance_mi, "mi")}, {weekDelta(t.week.gain_ft, lw.gain_ft, "ft up")}).
            </p> : null}
            {t.week.activities.length ? <ul className="today-activity-list">
              {t.week.activities.map((a) => <li key={a.id}>
                <Link href={`/activities/${a.id}`}>{a.name ?? "Activity"}</Link>
                <span>{dayLabel(a.day)} · {mi(a.distance_mi)} mi · {n0(a.gain_ft)} ft ↑ · {n0(a.loss_ft)} ft ↓</span>
              </li>)}
            </ul> : <p className="today-caption">No activities yet this week.</p>}
          </details>
        </section>
        <section className="today-intensity" aria-labelledby="today-intensity-title">
          <div className="today-section-head">
            <h2 id="today-intensity-title">Intensity, 28 days</h2>
            <span className="today-caption">Target 80% below AeT</span>
          </div>
          <ZoneBar share={t.zones_28d?.share ?? { ...t.intensity_28d.share }} model={t.zone_model} belowAet={t.intensity_28d.share.z1} />
        </section>
      </div>
      <aside className="today-rail" aria-label="Readiness and upcoming training">
        <section className="today-readiness" aria-labelledby="today-readiness-title">
          <h2 id="today-readiness-title">Your readiness</h2>
          {(["engine", "legs"] as const).map((key) => (
            <div className="today-readiness-row" key={key}>
              <div><h3>{key === "engine" ? "Engine" : "Legs"}</h3><p>{t.readiness[key].detail}</p></div>
              <span className={`today-readiness-state ${t.readiness[key].state}`}>{t.readiness[key].state === "fresh" ? "Ready" : stateLabel(t.readiness[key].state)}</span>
            </div>
          ))}
          <div className="today-sleep">
            <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden>
              <circle cx="12" cy="12" r="9" /><path d="M12 6v6l4 2" />
            </svg>
            <span>{sleep == null ? "Sleep data unavailable" : `Sleep · ${Math.floor(Math.round(sleep * 60) / 60)} h ${Math.round(sleep * 60) % 60} min`}</span>
          </div>
          <details className="today-disclosure">
            <summary>Readiness &amp; overnight details</summary>
            <ReadinessScore s={t.score} />
            <Overnight w={t.wellness} />
          </details>
        </section>
        <CheckIn initial={t.checkin} compact />
        <section className="today-upcoming" aria-labelledby="today-upcoming-title">
          <h2 id="today-upcoming-title">Coming up</h2>
          {upcoming.length ? <ul className="today-agenda">
            {upcoming.map((run) => <li key={run.key}>
              <div className="today-agenda-date"><span>{dayLabel(run.day)}</span><strong>{dayLabel(run.day, { day: "numeric" })}</strong></div>
              <div><Link href="/plan">{run.title}</Link><p>
                {run.minutes > 0 ? `${duration(run.minutes * 60)} · ` : ""}{mi(run.miles)} mi · {n0(run.gain_ft)} ft ↑ · {n0(run.loss_ft)} ft ↓
              </p></div>
            </li>)}
          </ul> : <p className="today-caption">Nothing scheduled yet. Plan your next session when you&apos;re ready.</p>}
          <Link className="today-plan-link" href="/plan">Open plan <span aria-hidden>›</span></Link>
          {t.ahead ? <details className="today-disclosure"><summary>Objective &amp; weekly targets</summary><ComingUp ahead={t.ahead} /></details> : null}
        </section>
      </aside>
    </div>
  );
}

function weekDelta(now: number, then: number, unit: string): string {
  const d = now - then;
  if (Math.abs(d) < (unit === "mi" ? 0.05 : 0.5)) return `same ${unit}`;
  return `${(unit === "mi" ? mi : n0)(Math.abs(d))} ${unit} ${d > 0 ? "more" : "less"}`;
}
