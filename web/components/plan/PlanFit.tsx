import AiCost from "@/components/AiCost";
import CoachText from "@/components/CoachText";
import CoachFeedback from "@/components/CoachFeedback";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { CoachReply, FitReply } from "@/lib/types";

const PARTS: [keyof FitReply, string][] = [["helps", "Does it help"], ["week", "This week"], ["next", "Next"]];

// The latest training read against the plan, written after each sync: did it do the planned
// session's job, where the week stands, and what to do with the rest of it.
export default async function PlanFit() {
  let r: CoachReply<FitReply>;
  try {
    r = await engine<CoachReply<FitReply>>("/api/coach/fit");
  } catch {
    return null;  // nothing recent to read, or no accepted plan
  }
  return (
    <section className="card">
      <h2 className="label">Your latest training and the plan</h2>
      <dl className="coach-read">
        {PARTS.map(([k, label]) => (
          <div key={k}>
            <dt>{label}</dt>
            <dd><CoachText text={r.reply[k]} /></dd>
          </div>
        ))}
      </dl>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div className="muted" style={{ fontSize: 12, maxWidth: 520 }}>{coachNote(r.source, r.fallback_reason, "read")}<AiCost cost={r.cost} /></div>
        {r.log_id ? <CoachFeedback logId={r.log_id} initial={r.feedback} /> : null}
      </div>
    </section>
  );
}
