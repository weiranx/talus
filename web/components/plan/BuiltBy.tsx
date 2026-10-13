"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Dots } from "@/components/Loading";
import type { Plan } from "@/lib/types";

// Who built the weeks: the AI coach, with the engine holding each week to its limits, or the engine alone.
// While the coach is still building, the engine's weeks show and the page checks back.
export default function BuiltBy({ plan }: { plan: Plan }) {
  const router = useRouter();
  useEffect(() => {
    if (!plan.building) return;
    const timer = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(timer);
  }, [plan.building, router]);
  if (plan.building) {
    return (
      <div role="status" className="muted" style={{ fontSize: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <Dots /><span>The AI coach is building its version of these weeks. Until it&apos;s ready, these are the engine&apos;s.</span>
      </div>
    );
  }
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
