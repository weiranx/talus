import { n0 } from "@/lib/format";
import type { Dim, DimStatus, Focus, SessionKind } from "@/lib/types";

export const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const DAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const FOCUS_TEXT: Record<Focus, string> = {
  build: "Build", peak: "Peak", recovery: "Recovery", taper: "Taper", race: "Objective week",
};

export const STATUS_TONE: Record<DimStatus, "good" | "warn" | "bad" | "neutral"> = {
  ready: "good", close: "warn", gap: "bad", unknown: "neutral", context: "neutral",
};

export const KIND_COLOR: Record<SessionKind, string> = {
  long: "var(--climb)", hills: "var(--descent)", easy: "var(--cardio)", rest: "var(--line)",
  objective: "var(--ink)", done: "var(--z1)",
};

// Units sit under the dimension's name, so the cells stay narrow.
export function dimValue(d: Dim, v: number | null): string {
  if (v == null) return "–";
  if (d.key === "altitude") return d.demand === v ? n0(v) : v.toFixed(1);
  if (d.unit === "h" || d.unit === "effort mi") return v.toFixed(1);
  return n0(v);
}

export function hours(h: number): string {
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

export function minutesText(m: number): string {
  const r = Math.round(m / 5) * 5;
  return r >= 60 ? `${Math.floor(r / 60)} h ${r % 60 ? `${r % 60} min` : ""}`.trim() : `${r} min`;
}
