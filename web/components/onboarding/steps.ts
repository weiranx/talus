import type { Onboarding, OnboardingStep } from "@/lib/types";

export const STEPS: { key: OnboardingStep; label: string; todo: string }[] = [
  { key: "connect", label: "Connect", todo: "Connect a data source" },
  { key: "import", label: "Import", todo: "Import your training history" },
  { key: "thresholds", label: "Heart rate", todo: "Set your heart rate thresholds" },
  { key: "sleep", label: "Sleep", todo: "Set the sleep you need" },
  { key: "coach", label: "Coach", todo: "Choose whether to use the AI coach" },
  { key: "tour", label: "Today", todo: "Take the tour of Today" },
];

export const isStep = (s: string | undefined): s is OnboardingStep => STEPS.some((x) => x.key === s);

export function nextStep(step: OnboardingStep): OnboardingStep | null {
  const i = STEPS.findIndex((s) => s.key === step);
  return STEPS[i + 1]?.key ?? null;
}

export function prevStep(step: OnboardingStep): OnboardingStep | null {
  const i = STEPS.findIndex((s) => s.key === step);
  return i > 0 ? STEPS[i - 1].key : null;
}

/** Steps that matter for Today's numbers and aren't done yet. */
export function unfinished(ob: Onboarding): typeof STEPS {
  const open = new Set(ob.steps.filter((s) => !s.done).map((s) => s.key));
  return STEPS.filter((s) => open.has(s.key) && ["connect", "import", "thresholds"].includes(s.key));
}

/** Record a step or the flow's end. Returns false when the engine refused. */
export async function mark(action: "start" | "done" | "skip" | "finish", step?: OnboardingStep,
  extra: Record<string, unknown> = {}): Promise<boolean> {
  const res = await fetch("/api/onboarding", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, step, ...extra }),
  }).catch(() => null);
  return Boolean(res?.ok);
}
