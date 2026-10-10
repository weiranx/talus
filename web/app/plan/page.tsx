import Link from "next/link";
import { Suspense } from "react";
import EngineDown from "@/components/EngineDown";
import Pill from "@/components/Pill";
import Calendar from "@/components/plan/Calendar";
import Chat from "@/components/plan/Chat";
import CoachPlan, { CoachPlanPending } from "@/components/plan/CoachPlan";
import ConstraintsForm from "@/components/plan/ConstraintsForm";
import DayCards from "@/components/plan/DayCards";
import DeletePlan from "@/components/plan/DeletePlan";
import ObjectiveForm from "@/components/plan/ObjectiveForm";
import PlanActions from "@/components/plan/PlanActions";
import Readiness from "@/components/plan/Readiness";
import Review from "@/components/plan/Review";
import ThisWeek from "@/components/plan/ThisWeek";
import WeeksTable from "@/components/plan/WeeksTable";
import { engine, EngineError } from "@/lib/api";
import { dayLabel, mi, n0 } from "@/lib/format";
import { FOCUS_TEXT } from "@/lib/plan";
import type { ChatMessage, Plan, PlanPage, Schedule } from "@/lib/types";

export const dynamic = "force-dynamic";

function weekStart(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const day = new Date(Date.UTC(y, m - 1, d));
  day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
  return day.toISOString().slice(0, 10);
}

function Draft({ draft, hasAccepted }: { draft: Plan; hasAccepted: boolean }) {
  return (
    <section className="card" style={{ outline: "2px solid var(--ink)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label">Drafted plan: {draft.reason_text.toLowerCase()}</h2>
        <Pill tone={draft.reachable ? "good" : "bad"}>{draft.reachable ? "Reachable inside the limits" : "Out of reach by this date"}</Pill>
      </div>
      {!draft.reachable ? (
        <div>
          The engine can&apos;t get you there inside the load limits by this date, and it won&apos;t loosen them.
          {draft.options.later ? ` It can by ${dayLabel(draft.options.later, { month: "long", day: "numeric", year: "numeric" })}.` : ""}
          {draft.options.smaller ? ` On this date, a version of about ${mi(draft.options.smaller.distance_mi)} mi with ${n0(Math.round(draft.options.smaller.gain_ft / 100) * 100)} ft up fits.` : ""}
        </div>
      ) : null}
      {hasAccepted ? (
        draft.changes.length ? (
          <div className="table-wrap">
            <table style={{ minWidth: 520 }}>
              <thead><tr><th scope="col">Week of</th><th scope="col">Was</th><th scope="col">Becomes</th></tr></thead>
              <tbody>
                {draft.changes.map((c) => (
                  <tr key={c.start}>
                    <td>{dayLabel(c.start, { month: "short", day: "numeric" })}</td>
                    <td className="mono" style={{ fontSize: 13 }}>{c.before ? `${FOCUS_TEXT[c.before.focus]}, ${mi(c.before.miles)} mi, ${n0(c.before.gain_ft)} ft up` : "–"}</td>
                    <td className="mono" style={{ fontSize: 13 }}>{FOCUS_TEXT[c.after.focus]}, {mi(c.after.miles)} mi, {n0(c.after.gain_ft)} ft up</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="muted">No week moves by more than 5% against your accepted plan.</div>
      ) : (
        <WeeksTable weeks={draft.weeks} current={draft.weeks[0].start} limit={8} />
      )}
      <div className="muted">Nothing changes until you accept. The coach&apos;s read of this draft is below.</div>
      <PlanActions draftId={draft.id} />
    </section>
  );
}

export default async function PlanPageView({ searchParams }: { searchParams: Promise<{ edit?: string }> }) {
  let p: PlanPage;
  let chat: { messages: ChatMessage[] };
  let schedule: Schedule;
  try {
    [p, chat, schedule] = await Promise.all([engine<PlanPage>("/api/plan"), engine<{ messages: ChatMessage[] }>("/api/coach/chat"),
      engine<Schedule>("/api/schedule")]);
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }
  const { edit } = await searchParams;
  const obj = p.objective;
  const plan = p.draft ?? p.accepted;

  if (!obj) {
    return (
      <>
        <header className="page-head"><h1 className="title">Plan</h1></header>
        <section className="card">
          <h2 className="label">Add an objective</h2>
          <div className="muted" style={{ maxWidth: 640 }}>
            A race or a mountain day with a date. Talus compares what it demands with what your last 8 weeks have proven,
            then builds weekly targets for miles, feet up and feet down to get you there, inside the same load limits as Today.
          </div>
          <ObjectiveForm current={null} />
        </section>
        <Calendar initial={schedule} />
        <section className="card">
          <h2 className="label">Training days</h2>
          <ConstraintsForm current={p.constraints} hasObjective={false} />
        </section>
        <div className="row"><div style={{ flex: "1 1 360px", minWidth: 0 }}><Chat initial={chat.messages} /></div></div>
      </>
    );
  }

  return (
    <>
      <header className="page-head">
        <div>
          <div className="eyebrow">
            {dayLabel(obj.day, { weekday: "long", month: "long", day: "numeric" })} · {obj.days_to_go} days to go
          </div>
          <h1 className="title">{obj.name}</h1>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8, paddingBottom: 6 }}>
          <div className="mono" style={{ fontSize: 14 }}>
            {mi(obj.distance_mi)} mi · {n0(obj.gain_ft)} ft up · {n0(obj.loss_ft)} ft down
          </div>
          <DeletePlan name={obj.name} />
        </div>
      </header>

      {p.draft ? <Draft draft={p.draft} hasAccepted={!!p.accepted} /> : null}

      <Calendar initial={schedule} />

      <div className="row">
        <div style={{ flex: "2 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          {p.accepted && p.this_week ? <ThisWeek w={p.this_week} /> : null}
          {p.accepted ? (
            <section className="card">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <h2 className="label">Next 7 days</h2>
                <span className="muted">Cut from each week&apos;s target, less what you&apos;ve done and your own runs</span>
              </div>
              <DayCards sessions={p.sessions} today={p.date} />
            </section>
          ) : null}
          {p.review ? <Suspense fallback={null}><Review fallback={p.review} /></Suspense> : null}
          {p.accepted ? (
            <section className="card">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <h2 className="label">Weekly targets</h2>
                <Link href="/plan/weeks">Full plan</Link>
              </div>
              <WeeksTable weeks={p.accepted.weeks} current={weekStart(p.date)} limit={6} actuals={p.actuals} />
              <PlanActions replan />
            </section>
          ) : null}
          {plan ? (
            <section className="card">
              <h2 className="label">Coach&apos;s read of the {p.draft ? "draft" : "plan"}</h2>
              <Suspense fallback={<CoachPlanPending />}><CoachPlan /></Suspense>
            </section>
          ) : (
            <section className="card">
              <h2 className="label">No plan yet</h2>
              <div className="muted">Talus builds the plan from your last four weeks of training. Sync your recent activities, then build it.</div>
              <PlanActions replan />
            </section>
          )}
        </div>
        <div style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          {p.readiness ? <Readiness r={p.readiness} projected={plan?.projected ?? null} note={p.fitness_only} /> : null}
          <Chat initial={chat.messages} />
        </div>
      </div>

      <div className="row">
        <details className="card" style={{ flex: "1 1 420px" }} open={edit === "objective"}>
          <summary className="label" style={{ cursor: "pointer", minHeight: 24 }}>Edit the objective</summary>
          <ObjectiveForm current={obj} />
          <div className="muted">
            Or <Link href="/plan?edit=new">start a different objective</Link>. Saving a new one retires this one and its plan.
          </div>
          {edit === "new" ? <ObjectiveForm current={obj} startNew /> : null}
        </details>
        <details className="card" style={{ flex: "1 1 420px" }} open={edit === "days"}>
          <summary className="label" style={{ cursor: "pointer", minHeight: 24 }}>Training days</summary>
          <ConstraintsForm current={p.constraints} hasObjective />
        </details>
      </div>
    </>
  );
}
