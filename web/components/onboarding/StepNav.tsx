"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { OnboardingStep } from "@/lib/types";
import { mark, nextStep, prevStep, STEPS } from "./steps";

// Back, Skip and Next for one step. Next records the step as done unless `markDone` is false
// (steps the engine works out on its own, like connecting a source). `blocked` says why Next
// can't be pressed yet. `finish` ends setup after the step and lands on Today.
export default function StepNav({ step, skippable, blocked, markDone = true, nextLabel, finish }: {
  step: OnboardingStep; skippable: boolean; blocked?: string | null; markDone?: boolean; nextLabel?: string; finish?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"next" | "skip" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const next = nextStep(step), prev = prevStep(step);
  const target = next && !finish ? `/welcome?step=${next}` : "/";

  async function go(action: "done" | "skip") {
    setBusy(action === "done" ? "next" : "skip");
    setError(null);
    if ((action === "skip" || markDone) && !(await mark(action, step))) {
      setBusy(null);
      return setError("Could not save that step. Is the engine running?");
    }
    if (finish) await mark("finish");
    router.push(target);
    router.refresh();
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="actions" style={{ justifyContent: "space-between" }}>
        {prev ? <Link href={`/welcome?step=${prev}`}>Back</Link> : <span />}
        <div className="actions">
          {skippable ? (
            <button className="btn light outline" onClick={() => go("skip")} disabled={busy !== null} aria-busy={busy === "skip"}>
              Skip for now
            </button>
          ) : null}
          <button className="btn" onClick={() => go("done")} disabled={busy !== null || Boolean(blocked)} aria-busy={busy === "next"}>
            {nextLabel ?? `Next: ${STEPS.find((s) => s.key === next)?.label.toLowerCase() ?? "Today"}`}
          </button>
        </div>
      </div>
      {blocked ? <div className="muted" style={{ textAlign: "right" }}>{blocked}</div> : null}
      {error ? <div className="error-text" role="status">{error}</div> : null}
    </div>
  );
}
