import AiCost from "@/components/AiCost";
import CoachText from "@/components/CoachText";
import CoachFeedback from "@/components/CoachFeedback";
import { BusyLine, Skel } from "@/components/Loading";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { CoachReply, PlanReply } from "@/lib/types";

const PARTS: [keyof PlanReply, string][] = [["verdict", "Verdict"], ["limiter", "Limiter"], ["plan", "The plan"]];

export function CoachPlanPending() {
  return (
    <>
      <dl className="coach-read" aria-hidden>
        {PARTS.map(([k, label], i) => (
          <div key={k}>
            <dt>{label}</dt>
            <dd style={{ display: "flex", alignItems: "center", minHeight: 20 }}><Skel w={`${88 - i * 12}%`} h={13} /></dd>
          </div>
        ))}
      </dl>
      <BusyLine>The coach is reading the plan.</BusyLine>
    </>
  );
}

// The coach's read of the plan: verdict, limiter and the plan's shape. It explains, never edits.
export default async function CoachPlan() {
  let r: CoachReply<PlanReply>;
  try {
    r = await engine<CoachReply<PlanReply>>("/api/coach/plan");
  } catch {
    return null;
  }
  return (
    <>
      <dl className="coach-read">
        {PARTS.map(([k, label]) => (
          <div key={k}>
            <dt>{label}</dt>
            <dd><CoachText text={r.reply[k]} /></dd>
          </div>
        ))}
      </dl>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div className="muted" style={{ fontSize: 12, maxWidth: 520 }}>{coachNote(r.source, r.fallback_reason, "summary")}<AiCost cost={r.cost} /></div>
        {r.log_id ? <CoachFeedback logId={r.log_id} initial={r.feedback} /> : null}
      </div>
    </>
  );
}
