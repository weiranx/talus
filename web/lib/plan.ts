import { n0 } from "@/lib/format";
import type { Dim, DimStatus, Focus, SessionKind, WeekProgress, Workout } from "@/lib/types";

export const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const FOCUS_TEXT: Record<Focus, string> = {
  build: "Build", peak: "Peak", recovery: "Recovery", taper: "Taper", race: "Objective week",
};

export const STATUS_TONE: Record<DimStatus, "good" | "warn" | "bad" | "neutral"> = {
  ready: "good", close: "warn", gap: "bad", unknown: "neutral", context: "neutral",
};

export const KIND_COLOR: Record<SessionKind, string> = {
  long: "var(--climb)", long2: "var(--climb)", hills: "var(--descent)", easy: "var(--cardio)", rest: "var(--line)",
  track: "var(--target)", tempo: "var(--target)", hill_repeats: "var(--descent)", race_pace: "var(--target)",
  objective: "var(--ink)", done: "var(--z1)",
};

// Units sit under the dimension's name, so the cells stay narrow.
export function dimValue(d: Dim, v: number | null): string {
  if (v == null) return "–";
  if (d.key === "altitude") return d.demand === v ? n0(v) : v.toFixed(1);
  if (d.unit === "h" || d.unit === "effort mi") return v.toFixed(1);
  return n0(v);
}

export const WORKOUT_TEXT: Record<Workout, string> = {
  none: "Default", track: "Track intervals", tempo: "Tempo", hill_repeats: "Hill repeats", race_pace: "Race pace",
};

export function hours(h: number): string {
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

export function minutesText(m: number): string {
  const r = Math.round(m / 5) * 5;
  return r >= 60 ? `${Math.floor(r / 60)} h ${r % 60 ? `${r % 60} min` : ""}`.trim() : `${r} min`;
}

export const VOL_DIMS = [
  { key: "miles", label: "Miles", unit: "mi" },
  { key: "gain_ft", label: "Gain", unit: "ft" },
  { key: "loss_ft", label: "Descent", unit: "ft" },
] as const;

export type CoverState = "covered" | "close" | "short" | "over";

// Done plus scheduled against the week's target, per dimension, and one word for the week:
// over when any dimension passes 115%, covered when every one reaches 90%, short when any
// falls under 70%.
export function cover(w: WeekProgress): { share: Record<"miles" | "gain_ft" | "loss_ft", number>; state: CoverState } | null {
  if (!w.target) return null;
  const share = Object.fromEntries(VOL_DIMS.map(({ key }) => {
    const have = (w.done?.[key] ?? 0) + w.ahead[key];
    return [key, w.target![key] > 0 ? have / w.target![key] : 1];
  })) as Record<"miles" | "gain_ft" | "loss_ft", number>;
  const vals = Object.values(share);
  const state = vals.some((v) => v > 1.15) ? "over" : vals.every((v) => v >= 0.9) ? "covered" : vals.some((v) => v < 0.7) ? "short" : "close";
  return { share, state };
}

// What each race type's plan does differently, for the plan page.
export const RACE_TYPE_TEXT: Record<string, string> = {
  road: " A road plan: a long run sized to the race, less vert, a weekly quality session and a taper.",
  hundred: " A 100-mile plan: long days well short of the race that practice power-hiking, race food and, every third build week, the dark; then a two-week taper.",
  multiday: " A multi-day plan: long days well short of the race that practice power-hiking, race food and, every third build week, the dark with a short sleep stop; then a two-week taper.",
};
