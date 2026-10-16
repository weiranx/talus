import Pill from "@/components/Pill";
import { hours, STATUS_TONE } from "@/lib/plan";
import { benchmark, BENCHMARK_STATUS, readinessValue } from "@/lib/readiness";
import type { PlanPage, ReadinessEval } from "@/lib/types";
import styles from "./readiness.module.css";

export default function Readiness({ r, projected, note, projectionCurrent = true }: {
  r: NonNullable<PlanPage["readiness"]>; projected: ReadinessEval | null; note: string; projectionCurrent?: boolean;
}) {
  const proj = new Map(projected?.dims.map((d) => [d.key, d]) ?? []);
  const missing = r.dims.some((d) => d.status === "unknown");
  const pairs = r.dims.some((d) => d.capacity_label.includes("back-to-back"));
  const t = r.demand.time;
  return (
    <section className="card">
      <div className={styles.heading}>
        <h2 className="label">Training benchmarks</h2>
        <Pill tone={missing && r.verdict === "ready" ? "neutral" : STATUS_TONE[r.verdict]}>
          {r.verdict === "ready" ? missing ? "Some benchmarks not assessed" : "Current benchmarks met" : "Current training gaps"}
        </Pill>
      </div>
      <p className="muted">Benchmarks are the model’s training targets, not proof you can complete the objective. They can be lower than the full race demand.</p>
      {pairs ? <p className="muted">Big-day size and time on feet use the larger of a single outing or 85% of a qualifying back-to-back pair’s combined total. A pair is not one continuous day.</p> : null}
      {r.dims.map((d) => {
        const p = proj.get(d.key);
        const target = benchmark(d);
        return <div key={d.key} className={styles.row}>
          <h3 className={styles.name}>{d.name}</h3>
          <p className={styles.basis}>{d.capacity_label}</p>
          <dl className={styles.values}>
            <div><dt>Race demand</dt><dd>{readinessValue(d, d.demand, true)}</dd></div>
            <div><dt>Training benchmark</dt><dd>{target == null ? d.rule == null ? "No benchmark" : "Not available" : readinessValue(d, target)}
              {target != null ? <span className={styles.detail}>{Math.round(d.rule! * 100)}% of race demand</span> : null}
            </dd></div>
            <div><dt>Recent training</dt><dd>{readinessValue(d, d.capacity)}
              <Pill tone={STATUS_TONE[d.status]}>{BENCHMARK_STATUS[d.status]}</Pill>
            </dd></div>
            {projected ? <div><dt>{projectionCurrent ? "Planned training" : "Original plan"}</dt><dd>{p ? readinessValue(p, p.capacity) : "Not available"}
              {p ? <Pill tone={projectionCurrent ? STATUS_TONE[p.status] : "neutral"}>{projectionCurrent ? BENCHMARK_STATUS[p.status] : "Needs updated draft"}</Pill> : null}
            </dd></div> : null}
          </dl>
        </div>;
      })}
      {projected ? <p className="muted">{projectionCurrent
        ? "Planned training uses the weeks before taper and assumes you complete and recover from those sessions. Recent outings can fall outside that future window, so a planned value can be lower than today’s."
        : "Calendar adjustments changed the training behind this projection. Rebuild a draft to update it."}</p> : null}
      <p className="muted">Estimated moving time {hours(t.low_h)} to {hours(t.high_h)}, from {t.pace_learned ? `your own ${r.pace.min_per_effort_mi.toFixed(1)} min per effort mile over the last 8 weeks` : "a typical pace until Talus has 3 longer days to learn yours"}, slowing on long days. Stops are not included.</p>
      <p className="muted">{note}</p>
    </section>
  );
}
