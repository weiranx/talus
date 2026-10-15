"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Dots } from "@/components/Loading";
import type { Plan } from "@/lib/types";

// Who built the weeks: the AI coach, with the engine holding each week to its limits, or the engine alone
// when the coach couldn't. While the coach is still drafting and checking, the page shows that instead of
// any weeks and checks back.
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
        <Dots /><span>The AI coach is drafting your plan, then checking it a second time. It shows here when it&apos;s ready, usually in a few minutes.</span>
      </div>
    );
  }
  if (plan.builder !== "coach") return null;
  const held = plan.held_weeks ?? 0;
  const check = plan.check;
  return (
    <div style={{ fontSize: 14, display: "flex", flexDirection: "column", gap: 6 }}>
      <div>
        <span style={{ fontWeight: 600 }}>Built by the AI coach.</span>{" "}
        {plan.approach ? <span>{plan.approach} </span> : null}
        <span className="muted">
          The engine checked every week against your load limits
          {held ? ` and held ${held} week${held === 1 ? "" : "s"} the coach asked for to the 10% build limit.` : "."}
        </span>
      </div>
      {check?.summary ? (
        <div>
          <span style={{ fontWeight: 600 }}>{check.revised ? "Second check, revised:" : "Second check:"}</span> {check.summary}
          {check.issues.length && !check.ok ? (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }} className="muted">
              {check.issues.map((t) => <li key={t}>{t}</li>)}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
