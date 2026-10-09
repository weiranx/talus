import Pill from "@/components/Pill";
import { dimValue, hours, STATUS_TONE } from "@/lib/plan";
import type { PlanPage, ReadinessEval } from "@/lib/types";

const UNIT: Record<string, string> = {
  big_day: "effort mi, one day", time_on_feet: "hours, one day", climbing_volume: "ft; you: ft a week",
  descent_durability: "ft, one descent", descent_volume: "ft; you: ft a week", altitude: "ft; you: hours up high",
};

// Objective readiness: what the route demands against what training has proven, by dimension.
export default function Readiness({ r, projected, note }: {
  r: NonNullable<PlanPage["readiness"]>; projected: ReadinessEval | null; note: string;
}) {
  const proj = new Map(projected?.dims.map((d) => [d.key, d]) ?? []);
  const limiter = r.dims.find((d) => d.key === r.limiter);
  const t = r.demand.time;
  return (
    <section className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Objective readiness</h2>
        <Pill tone={STATUS_TONE[r.verdict]}>{r.verdict === "ready" ? "Ready" : r.verdict === "close" ? "Close" : "Gaps to close"}</Pill>
      </div>
      <div className="table-wrap">
        <table style={{ minWidth: 340 }}>
          <thead>
            <tr>
              <th scope="col">Dimension</th>
              <th scope="col" className="num">Needs</th>
              <th scope="col" className="num">You</th>
              <th scope="col">Now</th>
              {projected ? <th scope="col">On the plan</th> : null}
            </tr>
          </thead>
          <tbody>
            {r.dims.map((d) => (
              <tr key={d.key} title={`${d.demand_label} against ${d.capacity_label.toLowerCase()}${d.rule ? `. Ready at ${Math.round(d.rule * 100)}%.` : "."}`}>
                <td style={{ whiteSpace: "normal" }}>{d.name}<div className="muted" style={{ fontSize: 12 }}>{UNIT[d.key]}</div></td>
                <td className="num">{dimValue(d, d.demand)}</td>
                <td className="num">{dimValue(d, d.capacity)}</td>
                <td><Pill tone={STATUS_TONE[d.status]}>{d.status_text}</Pill></td>
                {projected ? (
                  <td>{proj.get(d.key) ? <Pill tone={STATUS_TONE[proj.get(d.key)!.status]}>{proj.get(d.key)!.status_text}</Pill> : null}</td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {limiter ? (
        <div style={{ fontSize: 14 }}>
          <strong>Limiter: {limiter.name.toLowerCase()}.</strong>{" "}
          {limiter.capacity_label} is {Math.round((limiter.ratio ?? 0) * 100)}% of what the route asks; ready is {Math.round((limiter.rule ?? 0) * 100)}%.
        </div>
      ) : null}
      <div className="muted">
        Estimated time {hours(t.low_h)} to {hours(t.high_h)}, from {t.pace_learned ? `your own ${r.pace.min_per_effort_mi.toFixed(1)} min per effort mile over the last 8 weeks` : "a typical pace until Talus has 3 longer days to learn yours"}, slowing on long days.
        Weekly gain and loss are compared with your weekly training.
      </div>
      <div className="muted">{note}</div>
    </section>
  );
}
