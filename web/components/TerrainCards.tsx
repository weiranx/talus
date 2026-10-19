import type { ReactNode } from "react";
import Pill from "@/components/Pill";
import { duration, mi, n0 } from "@/lib/format";
import type { Durability, DurabilityHalf, GradeBand, Terrain } from "@/lib/types";

// Estimated vertical speed and observed gait comparisons against recent activity history.
// Also shows how the second half held up on grade-adjusted pace.

export function pace(minPerMi: number | null | undefined): string {
  if (!minPerMi) return "–";
  const s = Math.round(minPerMi * 60);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const signed = (v: number) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "0");

function VsUsual({ b }: { b: GradeBand }) {
  const u = b.usual;
  if (!u || u.rate_change_pct == null) return <span className="muted">Comparison unavailable</span>;
  const faster = u.rate_change_pct > 0;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <Pill tone="neutral">
        {Math.abs(u.rate_change_pct) < 1 ? "At median" : `${Math.abs(u.rate_change_pct)}% ${faster ? "above" : "below"} median`}
      </Pill>
      {u.fastest ? <Pill tone="neutral">90-day highest rate</Pill> : null}
      <span className="muted">median {n0(u.vert_rate_ft_h)} ft/h</span>
    </span>
  );
}

function Hr({ b }: { b: GradeBand }) {
  if (b.avg_hr == null) return <>–</>;
  const d = b.usual?.hr_change_bpm;
  return <>{Math.round(b.avg_hr)}{d != null && d !== 0 ? <span className="muted"> ({signed(d)})</span> : null}</>;
}

function BandRows({ title, bands }: { title: string; bands: GradeBand[] }) {
  if (!bands.length) return null;
  return (
    <>
      <tr><th scope="rowgroup" colSpan={8} style={{ paddingTop: 12 }}>{title}</th></tr>
      {bands.map((b) => (
        <tr key={b.key}>
          <td>{b.label}</td>
          <td className="num">{b.kind === "climb" ? "+" : "−"}{n0(b.vert_ft)}</td>
          <td className="num">{duration(b.time_s)}</td>
          <td className="num"><strong>{n0(b.vert_rate_ft_h)}</strong></td>
          <td><VsUsual b={b} /></td>
          <td className="num">{pace(b.pace_min_mi)}</td>
          <td className="num"><Hr b={b} /></td>
          <td className="num">{Math.round(b.hike_share * 100)}%</td>
        </tr>
      ))}
    </>
  );
}

export function GradeCard({ t, footer }: { t: Terrain; footer?: ReactNode }) {
  const climbs = t.bands.filter((b) => b.kind === "climb");
  const descents = t.bands.filter((b) => b.kind === "descent");
  if (!t.bands.length) return null;
  return (
    <section className="card">
      <h2 className="label">Climbing and descending by grade</h2>
      <div className="muted">
        Estimated vertical speed by grade band, compared with your median from similar activity types in the preceding 90 days. Comparisons require at least 3 prior activities in the band and do not account for effort or trail conditions.
      </div>
      <div className="table-wrap">
        <table style={{ minWidth: 700 }}>
          <thead>
            <tr>
              <th scope="col">Grade</th>
              <th scope="col" className="num">Vert, ft</th>
              <th scope="col" className="num">Time</th>
              <th scope="col" className="num">ft/h</th>
              <th scope="col">Vertical speed vs. median</th>
              <th scope="col" className="num">Pace, /mi</th>
              <th scope="col" className="num">HR</th>
              <th scope="col" className="num">Est. hiking share</th>
            </tr>
          </thead>
          <tbody>
            <BandRows title="Climbs" bands={climbs} />
            <BandRows title="Descents" bands={descents} />
          </tbody>
        </table>
      </div>
      {t.hike_or_run.length ? (
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ fontWeight: 600 }}>Observed running vs. hiking</div>
          <div className="muted">Observed within this activity on different segments. This does not predict the benefit or HR cost of switching gait on the same terrain.</div>
          {t.hike_or_run.map((h) => (
            <div key={h.key}>
              On <strong>{h.label}</strong> climbs, segments classified as running had{" "}
              {h.run_rate_gain_pct >= 0 ? <strong>{h.run_rate_gain_pct}% higher vertical speed</strong> : <strong>{-h.run_rate_gain_pct}% lower vertical speed</strong>} than those classified as hiking
              {" "}({n0(h.run_rate_ft_h)} against {n0(h.hike_rate_ft_h)} ft/h)
              {h.run_hr_cost_bpm != null ? <> with average HR <strong>{h.run_hr_cost_bpm >= 0 ? `${h.run_hr_cost_bpm} bpm higher` : `${-h.run_hr_cost_bpm} bpm lower`}</strong></> : null}.
            </div>
          ))}
        </div>
      ) : null}
      <div className="muted">
        ft/h is estimated vertical feet gained or lost per moving hour in that grade band. HR in parentheses is the difference from your recent median in the band. Hiking share is estimated from cadence, or speed when cadence is unavailable; activities recorded as hikes or walks are treated as hiking. Lower speed at lower HR does not by itself indicate worse performance.
        {footer ? <> {footer}</> : null}
      </div>
    </section>
  );
}

const efficiencyLabel = (value: number) => `${Math.abs(value)}% efficiency ${value < 0 ? "gain" : "drop"}`;
const movingTime = (seconds: number) => {
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
};
const paceValue = (value: number | null) => value == null ? "Unavailable" : `${pace(value)}/mi`;

function comparisonRows(a: DurabilityHalf, b: DurabilityHalf): [string, string, string, string][] {
  const delta = a.flat_pace_min_mi != null && b.flat_pace_min_mi != null
    ? Math.round(b.flat_pace_min_mi * 60) - Math.round(a.flat_pace_min_mi * 60) : null;
  const hr = (v: number | null) => v == null ? "Unavailable" : `${Math.round(v)} bpm`;
  const climb = (v: number | null) => v == null ? "Unavailable" : `${n0(v)} ft/h`;
  return [
    ["Grade-adjusted pace", paceValue(a.flat_pace_min_mi), paceValue(b.flat_pace_min_mi),
      delta == null ? "Unavailable" : delta === 0 ? "Unchanged" : `${Math.abs(delta)} sec/mi ${delta > 0 ? "slower" : "faster"}`],
    ["Average heart rate", hr(a.avg_hr), hr(b.avg_hr), a.avg_hr == null || b.avg_hr == null ? "Unavailable" : `${signed(Math.round(b.avg_hr) - Math.round(a.avg_hr))} bpm`],
    ["Uphill speed · grades ≥8%", climb(a.climb_rate_ft_h), climb(b.climb_rate_ft_h),
      a.climb_rate_ft_h == null || a.climb_rate_ft_h <= 0 || b.climb_rate_ft_h == null ? "Unavailable" : `${signed(Math.round((b.climb_rate_ft_h / a.climb_rate_ft_h - 1) * 100))}%`],
  ];
}

export function DurabilityCard({ d }: { d: Durability }) {
  const drift = d.decoupling_pct;
  const status = drift == null ? "Aerobic efficiency unavailable" : drift < 5 ? "Aerobic effort held steady" : drift < 10 ? "Aerobic efficiency declined" : "Aerobic efficiency declined substantially";
  const a = d.first, b = d.second;
  const paceDirection = a.flat_pace_min_mi == null || b.flat_pace_min_mi == null ? null : b.flat_pace_min_mi > a.flat_pace_min_mi ? "slowed" : b.flat_pace_min_mi < a.flat_pace_min_mi ? "quickened" : "held steady";
  const hrDirection = a.avg_hr == null || b.avg_hr == null ? null : b.avg_hr < a.avg_hr ? "fell" : b.avg_hr > a.avg_hr ? "rose" : "held steady";
  return (
    <section className="card">
      <h2 className="label">Durability</h2>
      <div style={{ fontWeight: 600 }}>{status}</div>
      <div style={{ fontSize: "clamp(1.5rem, 4vw, 2rem)", fontWeight: 650, fontVariantNumeric: "tabular-nums" }}>
        {drift == null ? "Not enough heart-rate evidence" : drift === 0 ? "Aerobic efficiency unchanged" : efficiencyLabel(drift)}
      </div>
      <div className="muted">
        {d.usual_decoupling_pct != null
          ? `Recent median: ${efficiencyLabel(d.usual_decoupling_pct)} across ${d.usual_runs} activities of the same type in the prior 90 days.`
          : "Recent median unavailable: not enough comparable activity history."}
      </div>
      {paceDirection && hrDirection ? <p>Grade-adjusted pace {paceDirection} while average heart rate {hrDirection}.{drift != null && Math.abs(drift) < 5 ? " Aerobic efficiency was nearly unchanged." : ""}</p> : null}
      <div className="table-wrap">
        <table>
          <caption className="muted" style={{ textAlign: "left" }}>Comparison by moving-time halves</caption>
          <thead><tr><th scope="col">Metric</th><th scope="col" className="num">First half</th><th scope="col" className="num">Second half</th><th scope="col" className="num">Change</th></tr></thead>
          <tbody>{comparisonRows(a, b).map(([label, first, second, change]) => (
            <tr key={label}><th scope="row" style={{ fontWeight: 400 }}>{label}</th><td className="num">{first}</td><td className="num">{second}</td><td className="num">{change}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <details>
        <summary>Compare terrain and duration</summary>
        <div className="table-wrap"><table>
          <thead><tr><th scope="col">Metric</th><th scope="col" className="num">First half</th><th scope="col" className="num">Second half</th></tr></thead>
          <tbody>
            <tr><th scope="row">Distance</th><td className="num">{mi(a.distance_mi)} mi</td><td className="num">{mi(b.distance_mi)} mi</td></tr>
            <tr><th scope="row">Elevation gain</th><td className="num">{n0(a.gain_ft)} ft</td><td className="num">{n0(b.gain_ft)} ft</td></tr>
            <tr><th scope="row">Moving time</th><td className="num">{movingTime(a.time_s)}</td><td className="num">{movingTime(b.time_s)}</td></tr>
          </tbody>
        </table></div>
      </details>
      <details className="muted">
        <summary>How this is calculated</summary>
        <p>Aerobic efficiency compares grade-adjusted speed per heartbeat between moving-time halves. A positive drop means lower efficiency in the second half; a gain means higher efficiency. Talus labels a drop below 5% as steady, 5% to below 10% as declined, and 10% or more as substantially declined. This assesses aerobic efficiency, not overall durability.</p>
        <p>Grade-adjusted pace estimates equivalent flat-ground pace based on slope. It does not fully account for terrain or conditions. The first 10 moving minutes are excluded from first-half heart rate and efficiency, but included in the displayed pace and duration, so the table alone will not reproduce the efficiency result.</p>
        <p>The recent median uses activities of the same type in the prior 90 days with an efficiency measurement; these are not matched by duration.</p>
      </details>
    </section>
  );
}
