import Link from "next/link";
import type { Onboarding, OnboardingStep } from "@/lib/types";
import { STEPS } from "./steps";

// Where you are in setup. Every step is a link, so you can go back or jump ahead.
export default function Stepper({ ob, step }: { ob: Onboarding; step: OnboardingStep }) {
  const state = Object.fromEntries(ob.steps.map((s) => [s.key, s]));
  return (
    <ol aria-label="Setup" style={{ display: "flex", gap: 8, listStyle: "none", padding: 0, margin: 0, flexWrap: "wrap" }}>
      {STEPS.map((s, i) => {
        const current = s.key === step;
        const done = state[s.key]?.done;
        return (
          <li key={s.key} aria-current={current ? "step" : undefined} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Link href={`/welcome?step=${s.key}`} style={{ color: "inherit", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 24, height: 24, borderRadius: 12, display: "inline-grid", placeItems: "center", fontSize: 13, fontWeight: 600,
                background: current ? "var(--strong)" : done ? "var(--good-ink)" : "var(--bg)", color: current || done ? "var(--on-strong)" : "var(--muted)" }}>
                {done && !current ? "✓" : i + 1}
              </span>
              <span style={{ fontWeight: current ? 600 : 400 }}>{s.label}</span>
            </Link>
            {i < STEPS.length - 1 ? <span className="muted" aria-hidden>›</span> : null}
          </li>
        );
      })}
    </ol>
  );
}
