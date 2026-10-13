import AiCost from "@/components/AiCost";
import CoachFeedback from "@/components/CoachFeedback";
import CoachText from "@/components/CoachText";
import { Spinner } from "@/components/Loading";
import Pill from "@/components/Pill";
import type { AiCost as Cost, Call } from "@/lib/types";

const CALL_TONE = { go: "good", modify: "warn", rest: "bad" } as const;
const CALL_TEXT = { go: "Go", modify: "Modify", rest: "Rest" } as const;

export default function CallCard({ call, note, logId, feedback, planned, pending, cost }: {
  call: Call; note: string; logId?: number | null; feedback?: number | null; planned?: boolean; pending?: boolean; cost?: Cost | null;
}) {
  return (
    <section className="card dark call-card" style={{ padding: "22px 24px 18px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 className="label">Today&apos;s call</h2>
        <Pill tone={CALL_TONE[call.call]}>{CALL_TEXT[call.call]}</Pill>
      </div>
      {planned ? <div style={{ color: "var(--feature-muted)", fontSize: 13, marginBottom: -8 }}>From your plan</div> : null}
      <div style={{ fontSize: 28, lineHeight: "33px", fontWeight: 700, letterSpacing: "-0.01em" }}>{call.session}</div>
      <CoachText text={call.detail} style={{ color: "var(--feature-soft)" }} />
      <CoachText className="small" text={`**Why:** ${call.why}`} style={{ color: "var(--feature-muted)" }} />
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div style={{ color: "var(--feature-muted)", fontSize: 12, maxWidth: 520, display: "flex", alignItems: "center", gap: 8 }} role={pending ? "status" : undefined}>
          {pending ? <Spinner /> : null}<span>{note}<AiCost cost={cost} /></span>
        </div>
        {logId ? <CoachFeedback logId={logId} initial={feedback ?? null} dark /> : null}
      </div>
    </section>
  );
}
