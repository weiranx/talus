import CoachFeedback from "@/components/CoachFeedback";
import PlanActions from "@/components/plan/PlanActions";
import { engine } from "@/lib/api";
import { coachNote } from "@/lib/coach";
import { dayLabel, mi, n0, pct } from "@/lib/format";
import { hours } from "@/lib/plan";
import type { CoachReply, ReviewReply, WeekReview } from "@/lib/types";

function Numbers({ r }: { r: WeekReview }) {
  const rows: [string, string, string | null, number | null | undefined][] = [
    ["Miles", mi(r.actual.miles), r.target ? mi(r.target.miles) : null, r.share?.miles],
    ["Gain, ft", n0(r.actual.gain_ft), r.target ? n0(r.target.gain_ft) : null, r.share?.gain_ft],
    ["Descent, ft", n0(r.actual.loss_ft), r.target ? n0(r.target.loss_ft) : null, r.share?.loss_ft],
    ["Time", hours(r.actual.hours), r.target ? hours(r.target.hours) : null, null],
  ];
  return (
    <div className="table-wrap">
      <table style={{ minWidth: 320 }}>
        <thead><tr><th scope="col" /><th scope="col" className="num">Done</th><th scope="col" className="num">Target</th><th scope="col" className="num">Of target</th></tr></thead>
        <tbody>
          {rows.map(([label, done, target, share]) => (
            <tr key={label}><td>{label}</td><td className="num">{done}</td><td className="num">{target ?? "–"}</td>
              <td className="num">{share == null ? "–" : pct(share)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Last week against its targets, with the coach's review. Built from engine numbers.
// `compact` leaves out the numbers table, for the Plan page where this week's progress and the
// weekly chart already carry them.
export default async function Review({ fallback, compact }: { fallback: WeekReview; compact?: boolean }) {
  let r: (CoachReply<ReviewReply> & { review: WeekReview }) | null = null;
  try {
    r = await engine<CoachReply<ReviewReply> & { review: WeekReview }>("/api/coach/review");
  } catch {
    r = null;
  }
  const review = r?.review ?? fallback;
  const Wrap = compact ? "div" : "section";
  return (
    <Wrap className={compact ? undefined : "card"}
      style={compact ? { display: "flex", flexDirection: "column", gap: 10, borderTop: "1px solid var(--line)", paddingTop: 14 } : undefined}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label">Last week, {dayLabel(review.start, { month: "short", day: "numeric" })} to {dayLabel(review.end, { month: "short", day: "numeric" })}</h2>
        {review.actual.below_aet_pct != null ? <span className="muted">{pct(review.actual.below_aet_pct)} of time below AeT</span> : null}
      </div>
      {compact ? null : <Numbers r={review} />}
      {review.flags.length ? <div className="muted">Flags: {review.flags.map((f) => `${f.ledger} ${f.text.toLowerCase()}`).join("; ")}.</div> : null}
      {r ? (
        <>
          <div>{r.reply.week}</div>
          <div>{r.reply.next}</div>
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
            <div className="muted" style={{ fontSize: 12, maxWidth: 520 }}>{coachNote(r.source, r.fallback_reason, "review")}</div>
            {r.log_id ? <CoachFeedback logId={r.log_id} initial={r.feedback} /> : null}
          </div>
        </>
      ) : null}
      {review.replan.length ? (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div><strong>A new plan is suggested.</strong> {review.replan_text.join(". ")}.</div>
          <PlanActions replan reason={review.replan[0]} />
        </div>
      ) : null}
    </Wrap>
  );
}
