import CoachFeedback from "@/components/CoachFeedback";
import { BusyLine, Skel } from "@/components/Loading";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { CoachReply, PlanReply } from "@/lib/types";

const PARTS: [keyof PlanReply, string][] = [["verdict", "Verdict"], ["limiter", "Limiter"], ["plan", "The plan"]];

export function CoachPlanPending() {
  return (
    <>
      <dl className="coach-read" style={{ margin: 0, display: "grid", gridTemplateColumns: "110px 1fr", gap: "8px 16px" }} aria-hidden>
        {PARTS.map(([k, label], i) => (
          <div key={k} style={{ display: "contents" }}>
            <dt style={{ fontWeight: 600 }}>{label}</dt>
            <dd style={{ margin: 0, display: "flex", alignItems: "center", minHeight: 20 }}><Skel w={`${88 - i * 12}%`} h={13} /></dd>
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
      <dl className="coach-read" style={{ margin: 0, display: "grid", gridTemplateColumns: "110px 1fr", gap: "8px 16px" }}>
        {PARTS.map(([k, label]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt style={{ fontWeight: 600 }}>{label}</dt>
            <dd style={{ margin: 0 }}>{r.reply[k]}</dd>
          </div>
        ))}
      </dl>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div className="muted" style={{ fontSize: 12, maxWidth: 520 }}>{coachNote(r.source, r.fallback_reason, "summary")}</div>
        {r.log_id ? <CoachFeedback logId={r.log_id} initial={r.feedback} /> : null}
      </div>
    </>
  );
}
