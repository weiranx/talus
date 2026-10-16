export type RevisionReason = "busy" | "too_hard" | "sore" | "terrain" | "availability";
export type DateAvailability = { minutes: number; terrain: "any" | "flat" | "hills"; locked: boolean };
export type SessionSnapshot = { day: string; kind: string; miles: number; gain_ft: number; loss_ft: number; minutes: number; benefit?: string | null; source?: "dated_snapshot" | "planned_reference" };
export type WorkoutOutcome = {
  day: string; intent: "met" | "partial" | "missed"; effort: number | null;
  reason: Exclude<RevisionReason, "availability"> | "other" | null;
  planned: SessionSnapshot; actual: Omit<SessionSnapshot, "day" | "kind">;
};
export type PlanEvidence = {
  label: string; activity_days: number; last_activity: string | null; missing: string[]; assumptions: string[];
  milestone: { day: string; kind: string; text: string } | null;
};
export type CoachingPlan = {
  date: string; sessions: SessionSnapshot[]; availability: Record<string, DateAvailability>; fixed_days: string[];
  outcomes: WorkoutOutcome[]; evidence: PlanEvidence; undo_id: number | null;
  suggestions: { reason: RevisionReason; text: string }[];
  reasons: { key: RevisionReason; label: string }[];
  recent_sessions: { day: string; planned: SessionSnapshot; completed: boolean; outcome: WorkoutOutcome | null; actual: WorkoutOutcome["actual"] }[];
};
export type RevisionPreview = {
  id: number; reason: RevisionReason; impact: string; availability: Record<string, DateAvailability>;
  changes: { day: string; before: SessionSnapshot; after: SessionSnapshot; why: string }[];
  weeks: { start: string; before: WorkoutOutcome["actual"]; after: WorkoutOutcome["actual"] }[];
};
export function addPlanDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export async function planRequest<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { method, signal, headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof value?.detail === "string" ? value.detail : "Could not save that change. Try again.");
  return value as T;
}
