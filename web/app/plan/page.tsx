import Link from "next/link";
import { Suspense } from "react";
import EngineDown from "@/components/EngineDown";
import Pill from "@/components/Pill";
import Calendar from "@/components/plan/Calendar";
import Chat from "@/components/plan/Chat";
import CoachPlan, { CoachPlanPending } from "@/components/plan/CoachPlan";
import ConstraintsForm from "@/components/plan/ConstraintsForm";
import DeletePlan from "@/components/plan/DeletePlan";
import ObjectiveForm from "@/components/plan/ObjectiveForm";
import PlanActions from "@/components/plan/PlanActions";
import Review from "@/components/plan/Review";
import PlanTalk from "@/components/plan/PlanTalk";
import Setup, { DraftDetail, type Step } from "@/components/plan/Setup";
import NextRun from "@/components/plan/NextRun";
import ReadinessCompact from "@/components/plan/ReadinessCompact";
import RoadChart from "@/components/plan/RoadChart";
import ThisWeek from "@/components/plan/ThisWeek";
import WeeksTable from "@/components/plan/WeeksTable";
import { engine, engineFailed } from "@/lib/api";
import { dayLabel, mi, n0 } from "@/lib/format";
import { FOCUS_TEXT, RACE_TYPE_TEXT } from "@/lib/plan";
import type { ChatMessage, Plan, PlanPage, Schedule, Talk } from "@/lib/types";

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

export default async function PlanPageView({ searchParams }: { searchParams: Promise<{ edit?: string; step?: string }> }) {
  const { edit, step } = await searchParams;
  let p: PlanPage;
  let chat: { messages: ChatMessage[] };
  let schedule: Schedule;
  try {
    [p, chat, schedule] = await Promise.all([engine<PlanPage>("/api/plan"), engine<{ messages: ChatMessage[] }>("/api/coach/chat"),
      engine<Schedule>("/api/schedule")]);
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }
  const obj = p.objective;
  const plan = p.draft ?? p.accepted;

  if ((!obj || !p.accepted) && !step) {
    // Until a plan is accepted: planning with the coach, a few questions that fill the plan's inputs.
    let talk: Talk;
    try {
      talk = await engine<Talk>("/api/plan/talk?mode=setup");
    } catch (e) {
      return <EngineDown message={engineFailed(e)} />;
    }
    return (
      <>
        <header className="page-head">
          <div>
            <div className="eyebrow">Plan with the coach</div>
            <h1 className="title">Plan</h1>
          </div>
          <div style={{ paddingBottom: 6 }}><Link href="/plan?step=objective">Skip to the form</Link></div>
        </header>
        <section className="card"><PlanTalk initial={talk} /></section>
        {talk.step === "draft" ? <DraftDetail p={p} /> : null}
        <Calendar initial={schedule} />
      </>
    );
  }

  if (!obj || !p.accepted) {
    // The form: objective, then training days, then review.
    const at: Step = !obj ? (step === "days" ? "days" : "objective")
      : step === "objective" || step === "days" ? step : "review";
    return (
      <>
        <header className="page-head">
          <div>
            <div className="eyebrow">Build a plan in three steps</div>
            <h1 className="title">Plan</h1>
          </div>
          <div style={{ paddingBottom: 6 }}><Link href="/plan">Plan with the coach instead</Link></div>
        </header>
        <Setup p={p} step={at} />
        <Calendar initial={schedule} />
        <div className="row"><div style={{ flex: "1 1 360px", minWidth: 0 }}><Chat initial={chat.messages} /></div></div>
      </>
    );
  }

  const talk = edit === "coach" ? await engine<Talk>("/api/plan/talk?mode=change").catch(() => null) : null;
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
          <div className="actions" style={{ gap: 6 }}>
            <Link className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} href="/plan?edit=coach">Change with the coach</Link>
            <Link className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} href="/plan?edit=objective">Edit objective</Link>
            <Link className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} href="/plan?edit=days">Training days</Link>
            <DeletePlan name={obj.name} />
          </div>
        </div>
      </header>

      {edit === "objective" || edit === "new" ? (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <h2 className="label">{edit === "new" ? "A different objective" : "Edit the objective"}</h2>
            <Link href="/plan">Close</Link>
          </div>
          <div className="muted">
            {edit === "new"
              ? "Saving a new objective retires this one and its plan."
              : <>Saving drafts a new plan to review against this one. Or <Link href="/plan?edit=new">start a different objective</Link>.</>}
          </div>
          <ObjectiveForm key={edit} current={obj} startNew={edit === "new"} next="/plan" />
        </section>
      ) : null}
      {edit === "coach" && talk ? (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <h2 className="label">Change the plan with the coach</h2>
            <Link href="/plan">Close</Link>
          </div>
          <PlanTalk initial={talk} />
        </section>
      ) : null}
      {edit === "days" ? (
        <section className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <h2 className="label">Training days</h2>
            <Link href="/plan">Close</Link>
          </div>
          <div className="muted">Saving drafts a new plan to review against this one.</div>
          <ConstraintsForm current={p.constraints} hasObjective next="/plan" />
        </section>
      ) : null}

      {p.draft ? <Draft draft={p.draft} hasAccepted={!!p.accepted} /> : null}

      <div className="row">
        <div style={{ flex: "3 1 420px", minWidth: 0, display: "flex" }}>
          <div style={{ flex: 1, display: "flex", flexDirection: "column" }}><NextRun sessions={p.sessions} today={p.date} /></div>
        </div>
        <div style={{ flex: "2 1 320px", minWidth: 0, display: "flex", flexDirection: "column" }}>
          {p.this_week ? <ThisWeek w={p.this_week} /> : null}
        </div>
      </div>

      <Calendar initial={schedule} compact />

      <div className="row">
        <section className="card" style={{ flex: "3 1 420px", minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <h2 className="label">Road to {obj.name}</h2>
            <Link href="/plan/weeks">Every week</Link>
          </div>
          <div className="muted" style={{ fontSize: 14 }}>
            Peak {mi(p.accepted.peak.miles)} mi and {n0(p.accepted.peak.gain_ft)} ft up a week. Lighter weeks are recovery and taper; this week is outlined.
            {p.accepted.back_to_back ? " Long weekends are back-to-back long days, so no single day has to carry the whole distance." : ""}
            {RACE_TYPE_TEXT[p.accepted.race_type ?? ""] ?? ""}
          </div>
          <RoadChart weeks={p.accepted.weeks} current={weekStart(p.date)} actuals={p.actuals} />
        </section>
        <div style={{ flex: "2 1 320px", minWidth: 0, display: "flex", flexDirection: "column" }}>
          {p.readiness ? <ReadinessCompact r={p.readiness} projected={p.accepted.projected} note={p.fitness_only} /> : null}
        </div>
      </div>

      <div className="row">
        <div style={{ flex: "3 1 420px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <h2 className="label">Coach</h2>
            <Suspense fallback={<CoachPlanPending />}><CoachPlan /></Suspense>
            {p.review ? <Suspense fallback={null}><Review fallback={p.review} compact /></Suspense> : null}
          </section>
        </div>
        <div style={{ flex: "2 1 320px", minWidth: 0 }}><Chat initial={chat.messages} /></div>
      </div>
    </>
  );
}
