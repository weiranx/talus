import Link from "next/link";
import { Suspense } from "react";
import Pill from "@/components/Pill";
import CoachPlan, { CoachPlanPending } from "@/components/plan/CoachPlan";
import ConstraintsForm from "@/components/plan/ConstraintsForm";
import DeletePlan from "@/components/plan/DeletePlan";
import ObjectiveForm from "@/components/plan/ObjectiveForm";
import PlanActions from "@/components/plan/PlanActions";
import Readiness from "@/components/plan/Readiness";
import WeeksTable from "@/components/plan/WeeksTable";
import { dayLabel, mi, n0 } from "@/lib/format";
import type { Plan, PlanPage } from "@/lib/types";

export type Step = "objective" | "days" | "review";
const STEPS: { key: Step; label: string }[] = [
  { key: "objective", label: "Objective" }, { key: "days", label: "Training days" }, { key: "review", label: "Review and accept" },
];

// The step bar: where you are, and a way back to any step you've reached.
function Stepper({ step, hasObjective }: { step: Step; hasObjective: boolean }) {
  const at = STEPS.findIndex((s) => s.key === step);
  return (
    <ol aria-label="Plan setup" style={{ display: "flex", gap: 8, listStyle: "none", padding: 0, margin: 0, flexWrap: "wrap" }}>
      {STEPS.map((s, i) => {
        const reachable = s.key !== "review" || hasObjective;
        const current = i === at;
        const inner = (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 24, height: 24, borderRadius: 12, display: "inline-grid", placeItems: "center", fontSize: 13, fontWeight: 600,
              background: current ? "var(--ink)" : i < at ? "var(--good-ink)" : "var(--bg)", color: current || i < at ? "#fff" : "var(--muted)" }}>
              {i < at ? "✓" : i + 1}
            </span>
            <span style={{ fontWeight: current ? 600 : 400 }}>{s.label}</span>
          </span>
        );
        return (
          <li key={s.key} aria-current={current ? "step" : undefined} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {current || !reachable ? <span style={{ color: reachable ? undefined : "var(--muted)" }}>{inner}</span>
              : <Link href={`/plan?step=${s.key}`} style={{ color: "inherit", textDecoration: "none" }}>{inner}</Link>}
            {i < STEPS.length - 1 ? <span className="muted" aria-hidden>›</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

// The plan in one paragraph, every number the engine's.
export function planSummary(plan: Plan, name: string): string {
  const taper = plan.weeks.find((w) => w.focus === "taper" || w.focus === "race");
  const longs = plan.weeks.map((w) => w.long).filter((l) => l !== null);
  const big = longs.reduce((a, b) => (b.effort_mi > (a?.effort_mi ?? 0) ? b : a), longs[0]);
  return [
    `${plan.weeks.length} weeks to ${name}.`,
    `You start from your last four weeks, about ${mi(plan.baseline.miles)} mi and ${n0(plan.baseline.gain_ft)} ft up a week,`,
    `and build to a peak of ${mi(plan.peak.miles)} mi with ${n0(plan.peak.gain_ft)} ft up and ${n0(plan.peak.loss_ft)} ft down,`,
    `with a recovery week after every three builds${taper ? `, then taper from ${dayLabel(taper.start, { month: "short", day: "numeric" })}` : ""}.`,
    big ? `Your biggest long day is ${mi(big.miles)} mi with ${n0(big.gain_ft)} ft up.` : "",
  ].join(" ").trim();
}

// Setting up a plan, one step at a time: the objective, the days you can train, then the
// drafted plan to review and accept. Shown until a plan is accepted.
export default function Setup({ p, step }: { p: PlanPage; step: Step }) {
  const obj = p.objective;
  const draft = p.draft;
  return (
    <section className="card" style={{ gap: 18 }}>
      <Stepper step={step} hasObjective={!!obj} />
      {step === "objective" ? (
        <>
          <div>
            <h2 style={{ fontSize: 20, fontWeight: 600 }}>What are you training for?</h2>
            <div className="muted" style={{ maxWidth: 640 }}>
              A race or a mountain day with a date. Upload the route, or type its distance and vert.
            </div>
          </div>
          <ObjectiveForm current={obj} next="/plan?step=days" submit="Next: training days" />
        </>
      ) : null}
      {step === "days" ? (
        <>
          <div>
            <h2 style={{ fontSize: 20, fontWeight: 600 }}>When can you train?</h2>
            <div className="muted" style={{ maxWidth: 640 }}>
              The plan only puts runs on these days, keeps each day inside its time limit, and puts the long day where you choose.
            </div>
          </div>
          <ConstraintsForm current={p.constraints} hasObjective={!!obj} next={obj ? "/plan?step=review" : "/plan?step=objective"}
            submit={obj ? "Next: draft my plan" : "Next"} />
          <div><Link href="/plan?step=objective">Back to the objective</Link></div>
        </>
      ) : null}
      {step === "review" && obj ? (
        draft ? (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
              <div>
                <h2 style={{ fontSize: 20, fontWeight: 600 }}>Your plan for {obj.name}</h2>
                <div className="muted">
                  {dayLabel(obj.day, { weekday: "long", month: "long", day: "numeric" })} · {obj.days_to_go} days to go ·{" "}
                  {mi(obj.distance_mi)} mi, {n0(obj.gain_ft)} ft up, {n0(obj.loss_ft)} ft down
                </div>
              </div>
              <Pill tone={draft.reachable ? "good" : "bad"}>{draft.reachable ? "Reachable inside the limits" : "Out of reach by this date"}</Pill>
            </div>
            <div style={{ maxWidth: 760 }}>{planSummary(draft, obj.name)}</div>
            {!draft.reachable ? (
              <div>
                The engine can&apos;t get you there inside the load limits by this date, and it won&apos;t loosen them.
                {draft.options.later ? ` It can by ${dayLabel(draft.options.later, { month: "long", day: "numeric", year: "numeric" })}.` : ""}
                {draft.options.smaller ? ` On this date, a version of about ${mi(draft.options.smaller.distance_mi)} mi with ${n0(Math.round(draft.options.smaller.gain_ft / 100) * 100)} ft up fits.` : ""}
                {" "}<Link href="/plan?step=objective">Change the objective</Link>, or accept the plan anyway.
              </div>
            ) : null}
            <div className="actions" style={{ alignItems: "center" }}>
              <PlanActions draftId={draft.id} acceptOnly />
              <Link href="/plan?step=objective">Change the objective</Link>
              <Link href="/plan?step=days">Change training days</Link>
            </div>
            <div className="row">
              <div style={{ flex: "2 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
                <h3 className="label">The first weeks</h3>
                <WeeksTable weeks={draft.weeks} current={draft.weeks[0].start} limit={6} />
                <h3 className="label">Coach&apos;s read</h3>
                <Suspense fallback={<CoachPlanPending />}><CoachPlan /></Suspense>
              </div>
              <div style={{ flex: "1 1 340px", minWidth: 0 }}>
                {p.readiness ? <Readiness r={p.readiness} projected={draft.projected} note={p.fitness_only} /> : null}
              </div>
            </div>
            <div className="muted">Nothing is on your calendar until you accept. You can change or delete the plan any time after.</div>
            <div><DeletePlan name={obj.name} /></div>
          </>
        ) : (
          <>
            <h2 style={{ fontSize: 20, fontWeight: 600 }}>No plan drafted yet</h2>
            <div className="muted">Talus builds the plan from your last four weeks of training. Sync your recent activities, then draft it.</div>
            <PlanActions replan />
            <div><DeletePlan name={obj.name} /></div>
          </>
        )
      ) : null}
    </section>
  );
}
