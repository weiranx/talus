import AiCost from "@/components/AiCost";
import CoachFeedback from "@/components/CoachFeedback";
import { BusyLine, Skel } from "@/components/Loading";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { AnalysisReply, CoachReply } from "@/lib/types";

const QUESTIONS: [keyof AnalysisReply, string][] = [
  ["job", "Did it do its job?"],
  ["off", "Where did it go off?"],
  ["cost", "What did it cost?"],
  ["changes", "What does it change?"],
  ["takeaway", "Takeaway"],
];

export function CoachReadPending() {
  return (
    <section className="card">
      <h2 className="label">Coach&apos;s read</h2>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "minmax(140px, 200px) 1fr", gap: "10px 20px" }} className="coach-read" aria-hidden>
        {QUESTIONS.map(([k, q], i) => (
          <div key={k} style={{ display: "contents" }}>
            <dt style={{ fontWeight: 600 }}>{q}</dt>
            <dd style={{ margin: 0, maxWidth: 680, display: "flex", alignItems: "center", minHeight: 20 }}><Skel w={`${90 - i * 9}%`} h={13} /></dd>
          </div>
        ))}
      </dl>
      <BusyLine>The coach is reading this workout.</BusyLine>
    </section>
  );
}

export default async function CoachRead({ id }: { id: string }) {
  let r: CoachReply<AnalysisReply>;
  try {
    r = await engine<CoachReply<AnalysisReply>>(`/api/coach/activity/${encodeURIComponent(id)}`);
  } catch {
    return null;
  }
  return (
    <section className="card">
      <h2 className="label">Coach&apos;s read</h2>
      <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "minmax(140px, 200px) 1fr", gap: "10px 20px" }} className="coach-read">
        {QUESTIONS.map(([k, q]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt style={{ fontWeight: 600 }}>{q}</dt>
            <dd style={{ margin: 0, maxWidth: 680 }}>{r.reply[k]}</dd>
          </div>
        ))}
      </dl>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div className="muted" style={{ fontSize: 12, maxWidth: 560 }}>{coachNote(r.source, r.fallback_reason, "summary")}<AiCost cost={r.cost} /></div>
        {r.log_id ? <CoachFeedback logId={r.log_id} initial={r.feedback} /> : null}
      </div>
    </section>
  );
}
