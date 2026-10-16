import CallCard from "@/components/CallCard";
import { unstable_rethrow } from "next/navigation";
import { Suspense } from "react";
import { SkelCard } from "@/components/Loading";
import RouteRecommendations from "@/components/RouteRecommendations";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import type { Call, CoachReply } from "@/lib/types";

// Streams in after the page: shows the rule-based call while the coach writes.
export default async function CoachCall({ rules }: { rules: Call & { planned?: boolean } }) {
  let r: CoachReply<Call> | null = null;
  try {
    r = await engine<CoachReply<Call>>("/api/coach/brief");
  } catch (e) {
    unstable_rethrow(e);
  }
  const call = r?.reply ?? rules;
  return <>
    <CallCard call={call} note={coachNote(r?.source ?? "rules", r?.fallback_reason ?? (r ? null : "api_error"), "call")}
      logId={r?.log_id} feedback={r?.feedback} cost={r?.cost} planned={rules.planned && call.session === rules.session} />
    <Suspense fallback={<SkelCard label={180} lines={2} />}><RouteRecommendations call={call.call} /></Suspense>
  </>;
}
