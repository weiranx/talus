import type { LedgerDay } from "@/lib/types";

export const LEDGER_LABEL = { cardio: "Cardio", distance: "Distance", climb: "Climb", descent: "Descent" } as const;

export const LEDGER_UNIT = {
  cardio: "Cardio load: 100 is one hour at your threshold heart rate.",
  distance: "Distance: miles covered, flat or steep. Climb and descent carry the vert.",
  climb: "Climb load: 1 is one foot gained on a moderate grade. Steeper feet count more.",
  descent: "Descent load: 1 is one foot lost on a moderate grade. Steeper feet count more.",
} as const;

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export const n0 = (v: number | null | undefined) => (v == null ? "–" : int.format(Math.round(v)));
export const mi = (v: number) => v.toFixed(1);
export const pct = (v: number) => `${Math.round(v * 100)}%`;
// A change with its sign: "+31%", "−8%".
export const signedPct = (v: number) => {
  const p = Math.round(v * 100);
  return p === 0 ? "0%" : `${p > 0 ? "+" : "−"}${Math.abs(p)}%`;
};

// Ledger numbers: distance reads in miles to a tenth, the other loads as whole numbers.
export const ledgerDecimals = (name: string) => (name === "distance" ? 1 : 0);
export const ledgerNum = (v: number | null | undefined, decimals = 0) =>
  v == null ? "–" : decimals ? v.toFixed(decimals) : n0(v);
export const ledgerUnit = (name: string) => (name === "distance" ? " mi" : "");

export function duration(seconds: number, withSeconds = false): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  return withSeconds ? `${h}:${mm}:${String(sec).padStart(2, "0")}` : `${h}:${mm}`;
}

export function dayLabel(iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short" }): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", opts);
}

export function stateLabel(state: string): string {
  return state === "overreached" ? "Overreached" : state === "loaded" ? "Loaded" : "Fresh";
}

export function ledgerBadge(d: { flags: { key: string; text: string }[]; vs_normal: number | null; building?: boolean }) {
  if (d.building) return { text: "Building your normal", tone: "neutral" } as const;
  const flag = d.flags.find((f) => f.key === "overreach" || f.key === "ramp") ?? d.flags[0];
  if (flag) return { text: flag.text, tone: flag.key === "overreach" ? "bad" : "warn" } as const;
  if (d.vs_normal == null) return { text: "No history", tone: "neutral" } as const;
  if (d.vs_normal > 0.15) return { text: "Loaded", tone: "warn" } as const;
  if (d.vs_normal > 0.05) return { text: "Building", tone: "good" } as const;
  if (d.vs_normal < -0.1) return { text: "Recovering", tone: "neutral" } as const;
  return { text: "Steady", tone: "good" } as const;
}

// Zone 1 easiest and quietest; the hardest zone has the most contrast with the page. CSS variables, so each theme
// sets its own ramp in globals.css (--z1..--z3, --z5-1..--z5-5). The route map resolves them with cssColor().
const ZONE_RAMPS: Record<number, string[]> = {
  3: ["var(--z1)", "var(--z2)", "var(--z3)"],
  5: ["var(--z5-1)", "var(--z5-2)", "var(--z5-3)", "var(--z5-4)", "var(--z5-5)"],
};

export const zoneColors = (n: number) => ZONE_RAMPS[n] ?? ZONE_RAMPS[3];
export const zoneKeys = (n: number) => Array.from({ length: n }, (_, i) => `z${i + 1}`);

// What a ledger chart draws and its tooltip reads, to two decimals. A year of four ledgers is sent
// to the browser with the page, so the rest of each day stays on the server.
export type ChartDay = Pick<LedgerDay, "day" | "load" | "fitness" | "fatigue" | "vs_normal_text" | "flags">;
const r2 = (v: number) => Math.round(v * 100) / 100;
export const chartDay = (d: LedgerDay): ChartDay => ({ day: d.day, load: r2(d.load), fitness: r2(d.fitness),
  fatigue: r2(d.fatigue), vs_normal_text: d.vs_normal_text, flags: d.flags });
