import AiCost from "@/components/AiCost";
import CoachText from "@/components/CoachText";
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
      <dl className="coach-read" aria-hidden>
        {QUESTIONS.map(([k, q], i) => (
          <div key={k}>
            <dt>{q}</dt>
            <dd style={{ display: "flex", alignItems: "center", minHeight: 20 }}><Skel w={`${90 - i * 9}%`} h={13} /></dd>
          </div>
        ))}
      </dl>
      <BusyLine>The coach is reading this workout.</BusyLine>
    </section>
  );
}

// Started by the page alongside the activity itself, so the coach's read isn't waiting its turn.
export function coachRead(id: string): Promise<CoachReply<AnalysisReply> | null> {
  return engine<CoachReply<AnalysisReply>>(`/api/coach/activity/${encodeURIComponent(id)}`).catch(() => null);
}

export default async function CoachRead({ reply }: { reply: Promise<CoachReply<AnalysisReply> | null> }) {
  const r = await reply;
  if (!r) return null;
  return (
    <section className="card">
      <h2 className="label">Coach&apos;s read</h2>
      <dl className="coach-read">
        {QUESTIONS.map(([k, q]) => (
          <div key={k}>
            <dt>{q}</dt>
            <dd><CoachText text={r.reply[k]} /></dd>
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
