import Pill from "@/components/Pill";
import { dimValue, hours, STATUS_TONE } from "@/lib/plan";
import type { PlanPage, ReadinessEval } from "@/lib/types";

const UNIT: Record<string, string> = {
  big_day: "effort mi", time_on_feet: "h", climbing_volume: "ft a week", descent_durability: "ft in one descent",
  descent_volume: "ft a week", altitude: "h up high",
};

// Readiness in one glance: the verdict, the limiter in a sentence, and one line per dimension
// with where you are now and where the plan takes you.
export default function ReadinessCompact({ r, projected, note, projectionCurrent = true }: {
  r: NonNullable<PlanPage["readiness"]>; projected: ReadinessEval | null; note: string; projectionCurrent?: boolean;
}) {
  const proj = new Map(projected?.dims.map((d) => [d.key, d]) ?? []);
  const limiter = r.dims.find((d) => d.key === r.limiter);
  const verdict = projected?.verdict ?? r.verdict;
  return (
    <section className="card" style={{ gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Ready on the day?</h2>
        <Pill tone={projectionCurrent ? STATUS_TONE[verdict] : "warn"}>{!projectionCurrent ? "Needs an updated draft" : verdict === "ready" ? "Conditional on the plan" : verdict === "close" ? "Close" : "Gaps left"}</Pill>
      </div>
      {limiter ? (
        <div style={{ fontSize: 14 }}>
          <strong>Furthest behind now: {limiter.name.toLowerCase()}.</strong>{" "}
          {limiter.capacity_label} is {Math.round((limiter.ratio ?? 0) * 100)}% of what the route asks; ready is {Math.round((limiter.rule ?? 0) * 100)}%.
        </div>
      ) : null}
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, fontSize: 12 }} className="muted">
          <span /><span style={{ width: 72 }}>Now</span><span style={{ width: 72 }}>{projectionCurrent ? "If completed" : "Original plan"}</span>
        </div>
        {r.dims.map((d) => {
          const p = proj.get(d.key);
          return (
            <div key={d.key} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, alignItems: "center", padding: "6px 0", borderTop: "1px solid var(--line)" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 14 }}>{d.name}</div>
                <div className="muted mono" style={{ fontSize: 12 }}>
                  {d.key === "altitude" ? `${d.capacity.toFixed(1)} h above 8,000 ft${d.demand == null ? "" : ` · objective ${Math.round(d.demand).toLocaleString()} ft`}` :
                    d.demand == null ? `${dimValue(d, d.capacity)} ${UNIT[d.key]}` : `${dimValue(d, d.capacity)} of ${dimValue(d, d.demand)} ${UNIT[d.key]}`}
                </div>
              </div>
              <span style={{ width: 72 }}><Pill tone={STATUS_TONE[d.status]}>{d.status_text}</Pill></span>
              <span style={{ width: 72 }}>{p ? <Pill tone={STATUS_TONE[p.status]}>{p.status_text}</Pill> : null}</span>
            </div>
          );
        })}
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {projectionCurrent ? "Projection assumes you complete and recover from the planned training. " : "Calendar adjustments changed the training behind this projection. Rebuild a draft to update it. "}
        Estimated time {hours(r.demand.time.low_h)} to {hours(r.demand.time.high_h)}. {note}
      </div>
    </section>
  );
}
