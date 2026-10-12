import type { AiCost as Cost } from "@/lib/types";

// What the AI call behind a coach reply cost. Only sent when the admin turns on "Show AI cost".
// Inherits the color of the note it sits under.
export function costText(c: Cost): string {
  const usd = c.usd < 0.01 ? `$${c.usd.toFixed(4)}` : `$${c.usd.toFixed(3)}`;
  const tokens = `${c.input_tokens.toLocaleString()} in, ${c.output_tokens.toLocaleString()} out`;
  return c.cached
    ? `AI cost $0 (reused; first call ${usd}, ${c.model ?? "unknown model"}, ${tokens})`
    : `AI cost ${usd} · ${c.model ?? "unknown model"} · ${tokens} tokens`;
}

export default function AiCost({ cost }: { cost?: Cost | null }) {
  if (!cost) return null;
  return <span className="mono" style={{ display: "block", fontSize: 11, marginTop: 2 }}>{costText(cost)}</span>;
}
