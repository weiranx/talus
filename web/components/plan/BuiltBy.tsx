import type { Plan } from "@/lib/types";

// Who built the weeks: the AI coach, with the engine holding each week to its limits, or the engine alone.
export default function BuiltBy({ plan }: { plan: Plan }) {
  if (plan.builder !== "coach") return null;
  const held = plan.held_weeks ?? 0;
  return (
    <div style={{ fontSize: 14 }}>
      <span style={{ fontWeight: 600 }}>Built by the AI coach.</span>{" "}
      {plan.approach ? <span>{plan.approach} </span> : null}
      <span className="muted">
        The engine checked every week against your load limits
        {held ? ` and held ${held} week${held === 1 ? "" : "s"} the coach asked for to the 10% build limit.` : "."}
      </span>
    </div>
  );
}
