import AiCost from "@/components/AiCost";
import CoachFeedback from "@/components/CoachFeedback";
import CoachText from "@/components/CoachText";
import { Spinner } from "@/components/Loading";
import { mi, n0 } from "@/lib/format";
import type { AiCost as Cost, Call, PlannedToday } from "@/lib/types";

const CALL_TEXT = { go: "Ready to train", modify: "Adjusted", rest: "Rest" } as const;

export default function CallCard({ call, note, logId, feedback, planned, pending, cost, scheduled }: {
  call: Call; note: string; logId?: number | null; feedback?: number | null; planned?: boolean; pending?: boolean; cost?: Cost | null; scheduled?: PlannedToday | null;
}) {
  const session = planned ? scheduled : null;
  return (
    <section className="today-session" aria-labelledby="today-session-title">
      <div className="today-session-top">
        <span>Recommended session{planned ? " · From your plan" : ""}</span>
        <span className={`today-call-state ${call.call}`}>{CALL_TEXT[call.call]}</span>
      </div>
      <h2 id="today-session-title">{call.session}</h2>
      <CoachText className="today-session-guidance" text={call.detail} />
      {session && session.kind !== "rest" ? (
        <dl className="today-session-stats">
          {session.minutes != null && session.minutes > 0 ? <div><dt>Duration</dt><dd>{session.minutes} <span>min</span></dd></div> : null}
          <div><dt>Distance</dt><dd>{mi(session.miles)} <span>mi</span></dd></div>
          <div><dt>Ascent · {n0(session.loss_ft)} ft descent</dt><dd>{n0(session.gain_ft)} <span>ft ↑</span></dd></div>
        </dl>
      ) : null}
      <div className="today-session-actions">
        <details className="today-session-view">
          <summary className="btn">View session <span aria-hidden>↗</span></summary>
          <div className="today-session-expanded">
            <h3>Session guidance</h3><CoachText text={call.detail} />
            {call.focus ? <><h3>Focus</h3><CoachText text={call.focus} /></> : null}
            {call.ahead ? <><h3>Ahead</h3><CoachText text={call.ahead} /></> : null}
            {session?.benefit ? <><h3>Training benefit</h3><p>{session.benefit}</p></> : null}
          </div>
        </details>
        <details className="today-session-why">
          <summary>Why this session <span aria-hidden>›</span></summary>
          <CoachText text={call.why} />
        </details>
      </div>
      {scheduled && !planned && scheduled.kind !== "rest" ? (
        <div className="today-session-change">
          <span>Planned: <strong>{scheduled.title}</strong></span><span aria-hidden>→</span>
          <span>Today: <strong>{call.session}</strong></span>
          <p>{call.call === "rest" ? "Skip the planned session or move it to another day." : "Follow today's recommendation in place of the scheduled session."}</p>
        </div>
      ) : null}
      <div className="today-session-footer">
        <span role={pending ? "status" : undefined}>{pending ? <Spinner /> : null} {note}<AiCost cost={cost} /></span>
        {logId ? <CoachFeedback logId={logId} initial={feedback ?? null} /> : null}
      </div>
    </section>
  );
}
