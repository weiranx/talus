import AiCost from "@/components/AiCost";
import CoachText from "@/components/CoachText";
import CoachFeedback from "@/components/CoachFeedback";
import { BusyLine, Skel } from "@/components/Loading";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { CoachReply, LoadReply } from "@/lib/types";

const QUESTIONS: [keyof LoadReply, string][] = [
  ["last_week", "Last week"],
  ["trend", "The trend"],
  ["watch", "Watch"],
  ["next", "Next 7 days"],
];

export function LoadReadPending() {
  return (
    <section className="card">
      <h2 className="label">Coach&apos;s take</h2>
      <dl className="coach-read" aria-hidden>
        {QUESTIONS.map(([k, q], i) => (
          <div key={k}>
            <dt>{q}</dt>
            <dd style={{ display: "flex", alignItems: "center", minHeight: 20 }}><Skel w={`${88 - i * 10}%`} h={13} /></dd>
          </div>
        ))}
      </dl>
      <BusyLine>The coach is reading your weeks.</BusyLine>
    </section>
  );
}

// Started alongside the page's own data, so the charts don't wait on the coach.
export function loadRead(): Promise<CoachReply<LoadReply> | null> {
  return engine<CoachReply<LoadReply>>("/api/coach/load").catch(() => null);
}

// The coach's take on the Load page: last week, where things are heading, what to watch and what
// the next 7 days should hold.
export default async function LoadRead({ reply }: { reply: Promise<CoachReply<LoadReply> | null> }) {
  const r = await reply;
  if (!r) return null;
  return (
    <section className="card">
      <h2 className="label">Coach&apos;s take</h2>
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
