import CoachFeedback from "@/components/CoachFeedback";
import Pill from "@/components/Pill";
import type { Call } from "@/lib/types";

const CALL_TONE = { go: "good", modify: "warn", rest: "bad" } as const;
const CALL_TEXT = { go: "Go", modify: "Modify", rest: "Rest" } as const;

export default function CallCard({ call, note, logId, feedback }: {
  call: Call; note: string; logId?: number | null; feedback?: number | null;
}) {
  return (
    <section className="card dark" style={{ padding: "22px 24px 18px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 className="label">Today&apos;s call</h2>
        <Pill tone={CALL_TONE[call.call]}>{CALL_TEXT[call.call]}</Pill>
      </div>
      <div style={{ fontSize: 28, lineHeight: "33px", fontWeight: 700, letterSpacing: "-0.01em" }}>{call.session}</div>
      <div style={{ color: "#e4eae7", maxWidth: 620 }}>{call.detail}</div>
      <div style={{ color: "#b9c6c0", fontSize: 14, maxWidth: 620 }}>Why: {call.why}</div>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div style={{ color: "#b9c6c0", fontSize: 12, maxWidth: 520 }}>{note}</div>
        {logId ? <CoachFeedback logId={logId} initial={feedback ?? null} dark /> : null}
      </div>
    </section>
  );
}
