import type { Dim, DimStatus } from "./types";

export const BENCHMARK_STATUS: Record<DimStatus, string> = {
  ready: "Benchmark met", close: "Near benchmark", gap: "Below benchmark",
  unknown: "Not assessed", context: "Context only",
};

export function benchmark(d: Dim): number | null {
  return d.demand != null && Number.isFinite(d.demand) && d.demand > 0
    && d.rule != null && Number.isFinite(d.rule) && d.rule > 0 ? d.demand * d.rule : null;
}

export function readinessValue(d: Dim, value: number | null, demand = false): string {
  if (value == null || !Number.isFinite(value)) return "Not available";
  // Altitude demand is feet; training evidence is hours, even if the numbers match.
  if (d.key === "altitude") return demand ? `${Math.round(value).toLocaleString("en-US")} ft` : `${value.toFixed(1)} h above 8,000 ft`;
  const unit = d.key === "climbing_volume" || d.key === "descent_volume" ? "ft/week" : d.unit;
  return `${unit === "h" || unit === "effort mi" ? value.toFixed(1) : Math.round(value).toLocaleString("en-US")} ${demand && unit === "ft/week" ? "ft" : unit ?? ""}`.trim();
}
