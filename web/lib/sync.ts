import { dayLabel } from "@/lib/format";
import type { LastSync, Settings, SyncResult } from "@/lib/types";

export const RANGES = [7, 30, 90, 180];

export function when(iso: string): string {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export function syncLine(s: LastSync): string {
  if (s.error) return `Failed ${when(s.at)}: ${s.error}`;
  return `${s.new ? `${s.new} new` : "No new activities"} · ${when(s.at)}`;
}

type StravaRun = NonNullable<Settings["sources"]["strava_web"]["last_sync"]>;

export function stravaLine(s: StravaRun): string {
  if (s.error_code === "session_expired") return `Stopped ${when(s.at)}: Strava ended the session. Copy a fresh one in the Strava card.`;
  if (s.error_code === "captcha") return `Stopped ${when(s.at)}: Strava asked for a captcha. Open strava.com in your browser, then copy a fresh session.`;
  // The engine's message, without the exception's class name or the command-line hint.
  if (s.error) return `Stopped ${when(s.at)}: ${s.error.replace(/^\w+: /, "").replace(/ ?Run `talus strava-web setup`[^.]*\./, "")}`;
  const here = s.aliased ? `, ${s.aliased} already here` : "";
  return `${when(s.at)}: ${s.listed} on Strava since ${dayLabel(s.since, { month: "short", day: "numeric" })}, ${s.saved} new${here}.`;
}

/** What a sync or watch import brought in, as one line for a list of links. */
export type Added = { id: number; name: string | null; start_time: string | null; distance_m: number | null; gain_m: number | null };

/** A partial sync must never claim everything is up to date. */
export function syncSummary(r: SyncResult): { ok: boolean; text: string } {
  const failed = r.sources.filter((s) => s.error).map((s) => s.label);
  const added = r.saved ? `${r.saved} new ${r.saved === 1 ? "activity" : "activities"} added.` : "";
  if (failed.length) return {
    ok: false,
    text: `${added ? `${added} ` : ""}Sync incomplete. ${failed.join(" and ")} couldn't sync. Check the source below and try again.`,
  };
  if (r.error) return { ok: false, text: `${added ? `${added} ` : ""}Sync incomplete. ${r.error}` };
  return { ok: true, text: added ? `Sync complete. ${added}` : "You're up to date. No new activities." };
}

export function addedLine(a: Added): string {
  const parts: string[] = [];
  if (a.start_time) parts.push(new Date(a.start_time).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }));
  if (a.name) parts.push(a.name);
  if (a.distance_m != null) parts.push(`${(a.distance_m / 1609.344).toFixed(1)} mi`);
  if (a.gain_m != null) parts.push(`${Math.round(a.gain_m * 3.28084).toLocaleString("en-US")} ft up`);
  return parts.join(" · ");
}

/** Sync every connected source, and turn the answer into a status line. */
export async function syncAll(days: number): Promise<{ ok: boolean; text: string; result: SyncResult | null }> {
  const res = await fetch("/api/sync", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ days }),
  }).catch(() => null);
  if (!res) return { ok: false, text: "Couldn't reach Talus. Check your connection and try again.", result: null };
  const body = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, text: typeof body.detail === "string" ? body.detail : `Sync couldn't finish (${res.status}). Try again.`, result: null };
  const r = body as SyncResult;
  return { ...syncSummary(r), result: r };
}
