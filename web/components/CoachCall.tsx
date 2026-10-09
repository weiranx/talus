import CallCard from "@/components/CallCard";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { Call, CoachReply } from "@/lib/types";

// Streams in after the page: shows the rule-based call while the coach writes.
export default async function CoachCall({ rules }: { rules: Call }) {
  let r: CoachReply<Call>;
  try {
    r = await engine<CoachReply<Call>>("/api/coach/brief");
  } catch {
    return <CallCard call={rules} note={coachNote("rules", "api_error", "call")} />;
  }
  return <CallCard call={r.reply} note={coachNote(r.source, r.fallback_reason, "call")} logId={r.log_id} feedback={r.feedback} />;
}
