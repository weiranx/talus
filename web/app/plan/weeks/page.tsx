import Link from "next/link";
import EngineDown from "@/components/EngineDown";
import WeeksTable from "@/components/plan/WeeksTable";
import { engine, EngineError } from "@/lib/api";
import { dayLabel, mi, n0 } from "@/lib/format";
import type { PlanPage } from "@/lib/types";

export const dynamic = "force-dynamic";

// The whole plan to the objective date, one row per week.
export default async function FullPlan() {
  let p: PlanPage;
  try {
    p = await engine<PlanPage>("/api/plan");
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }
  const plan = p.accepted ?? p.draft;
  if (!p.objective || !plan) {
    return <div className="empty"><p>No plan yet. <Link href="/plan">Add an objective</Link>.</p></div>;
  }
  const today = p.date;
  const current = plan.weeks.find((w) => w.start <= today && today < (plan.weeks[w.index + 1]?.start ?? "9999"))?.start ?? plan.weeks[0].start;
  return (
    <>
      <header className="page-head">
        <div>
          <div className="eyebrow"><Link href="/plan">Plan</Link> · {p.accepted ? "accepted" : "draft"}, built {dayLabel(plan.built_on, { month: "short", day: "numeric" })}</div>
          <h1 className="title">{p.objective.name}: every week</h1>
        </div>
      </header>
      <section className="card">
        <div className="muted">
          Peak weeks aim at {mi(plan.peak.miles)} mi, {n0(plan.peak.gain_ft)} ft up and {n0(plan.peak.loss_ft)} ft down. Build weeks add at most 10%,
          a recovery week follows every three builds, and every week was checked against the load limits by projecting your ledgers forward.
          Starting point: your last four weeks, {mi(plan.baseline.miles)} mi and {n0(plan.baseline.gain_ft)} ft up a week.
        </div>
        <WeeksTable weeks={plan.weeks} current={current} full />
      </section>
    </>
  );
}
