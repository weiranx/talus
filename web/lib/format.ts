export const LEDGER_LABEL = { cardio: "Cardio", climb: "Climb", descent: "Descent" } as const;

export const LEDGER_UNIT = {
  cardio: "Cardio load: 100 is one hour at your threshold heart rate.",
  climb: "Climb load: 1 is one foot gained on a moderate grade. Steeper feet count more.",
  descent: "Descent load: 1 is one foot lost on a moderate grade. Steeper feet count more.",
} as const;

const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export const n0 = (v: number | null | undefined) => (v == null ? "–" : int.format(Math.round(v)));
export const mi = (v: number) => v.toFixed(1);
export const pct = (v: number) => `${Math.round(v * 100)}%`;

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

// Light to dark: zone 1 easiest. The 3-zone ramp matches the --z1..--z3 colors.
const ZONE_RAMPS: Record<number, string[]> = {
  3: ["#a9bdb5", "#52605a", "#15211d"],
  5: ["#c4d2cb", "#93a9a0", "#68796f", "#3c4842", "#15211d"],
};

export const zoneColors = (n: number) => ZONE_RAMPS[n] ?? ZONE_RAMPS[3];
export const zoneKeys = (n: number) => Array.from({ length: n }, (_, i) => `z${i + 1}`);
