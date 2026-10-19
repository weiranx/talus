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

const VERDICT = {
  held: { tone: "good", text: "Held" },
  drifted: { tone: "warn", text: "Drifted" },
  faded: { tone: "bad", text: "Faded" },
} as const;

function halfRows(a: DurabilityHalf, b: DurabilityHalf): [string, string, string][] {
  const rows: [string, string, string][] = [
    ["Distance and vert", `${mi(a.distance_mi)} mi, +${n0(a.gain_ft)} ft`, `${mi(b.distance_mi)} mi, +${n0(b.gain_ft)} ft`],
    ["Moving time", duration(a.time_s), duration(b.time_s)],
    ["Grade-adjusted pace", `${pace(a.flat_pace_min_mi)} /mi`, `${pace(b.flat_pace_min_mi)} /mi`],
  ];
  if (a.climb_rate_ft_h && b.climb_rate_ft_h) rows.push(["Climbing over 8%", `${n0(a.climb_rate_ft_h)} ft/h`, `${n0(b.climb_rate_ft_h)} ft/h`]);
  if (a.avg_hr && b.avg_hr) rows.push(["Average HR", `${Math.round(a.avg_hr)}`, `${Math.round(b.avg_hr)}`]);
  return rows;
}

function summary(d: Durability): string {
  if (d.decoupling_pct != null) {
    const usual = d.usual_decoupling_pct != null ? ` Your usual on runs this long is ${signed(d.usual_decoupling_pct)}%.` : "";
    if (d.decoupling_pct <= 0) return `No drift: heart rate held or fell against grade-adjusted pace (${signed(d.decoupling_pct)}%).${usual}`;
    return `Heart rate drifted ${d.decoupling_pct}% against grade-adjusted pace. Under 5% is a held aerobic effort; over 10% is a fade.${usual}`;
  }
  if (d.pace_change_pct != null) {
    return d.pace_change_pct > 0
      ? `Grade-adjusted pace slowed ${d.pace_change_pct}% in the second half. No heart rate, so drift can't be read.`
      : `Grade-adjusted pace held or got quicker in the second half (${signed(d.pace_change_pct)}%).`;
  }
  return "";
}

export function DurabilityCard({ d }: { d: Durability }) {
  const v = d.verdict ? VERDICT[d.verdict] : null;
  return (
    <section className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label">Durability</h2>
        {v ? <Pill tone={v.tone}>{v.text}</Pill> : null}
      </div>
      <div>{summary(d)}</div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th scope="col" /><th scope="col" className="num">First half</th><th scope="col" className="num">Second half</th></tr>
          </thead>
          <tbody>
            {halfRows(d.first, d.second).map(([k, a, b]) => (
              <tr key={k}><td>{k}</td><td className="num">{a}</td><td className="num">{b}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="muted">
        Grade-adjusted pace ({pace(d.flat_pace_min_mi)} /mi overall) is what each mile would have been on the flat for the same heart rate, so climbing out and running home compare fairly. The first 10 minutes are left out of the heart-rate read while it warms up.
      </div>
    </section>
  );
}
