import Link from "next/link";
import ActivitiesTabs from "@/components/ActivitiesTabs";
import EngineDown from "@/components/EngineDown";
import ProgressRefresh from "./refresh";
import { engine, engineFailed } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import styles from "./progress.module.css";

export const dynamic = "force-dynamic";

type Evidence = { id: number; day: string; name: string; value: number };
type Signal = {
  metric: "route" | "climb" | "durability"; title: string; unit: string;
  latest: number | null; baseline?: number; change?: number; matches?: number;
  hr?: number; baseline_hr?: number; band?: string | null; evidence?: Evidence[];
};
type Progress = {
  since: string; through: string; activities: number; signals: Signal[];
  analysis: { ready: number; total: number; running: boolean; waiting_for: string | null };
  routes_pending: number;
};

function value(n: number, metric: Signal["metric"]): string {
  if (metric === "route") {
    const seconds = Math.round(n * 60);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  return metric === "climb" ? Math.round(n).toLocaleString("en-US") : `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
}

function observation(s: Signal): string {
  const change = s.change ?? 0;
  if (Math.abs(change) < 1) return "Little observed change";
  const amount = Math.abs(change).toFixed(1);
  if (s.metric === "durability") return `${amount} percentage points ${change < 0 ? "less" : "more"} slowdown`;
  return `${amount}% ${s.metric === "climb" ? (change > 0 ? "higher climbing speed" : "lower climbing speed") : (change < 0 ? "faster pace" : "slower pace")}`;
}

const rules = {
  route: "Same GPS route and direction, distance within 5%, average heart rate within 5 bpm.",
  climb: "Same grade band and activity type, heart rate within 5 bpm, total duration within 20%, and hiking share within 15 percentage points. At least 10 minutes climbing in the band.",
  durability: "Same GPS route and direction, distance within 5%, duration within 20%, and average heart rate within 5 bpm. Compares second-half grade-adjusted pace with the first half.",
};

export default async function ProgressPage() {
  let data: Progress;
  try { data = await engine<Progress>("/api/progress"); }
  catch (e) { return <EngineDown message={engineFailed(e)} />; }
  const available = data.signals.filter((s) => s.latest !== null).length;
  return <>
    <header className="page-head">
      <div><h1 className="title">Am I improving?</h1><p className="muted">Your performance, compared with your own earlier outings.</p></div>
      <ActivitiesTabs current="progress" />
    </header>
    <section className="card">
      <h2 className={styles.heading}>{available ? `${available} comparisons to explore` : "Building your comparison history"}</h2>
      <p>{available ? "Look for repeated changes across comparable outings. One faster day is an observation, not proof of improved fitness." : "Each comparison needs a recent outing and at least three earlier matches on different days. Missing evidence stays unavailable."}</p>
      <p className="muted">{data.activities} eligible activities · {dayLabel(data.since)} – {dayLabel(data.through)}. Latest comparisons must be from the last 30 days.</p>
      {data.analysis.running || data.routes_pending > 0 ? <p role="status">Preparing activity evidence: {data.analysis.ready} of {data.analysis.total} activity analyses ready; {data.routes_pending} route traces remaining. Refresh to check progress.</p> : null}
      {data.analysis.waiting_for === "thresholds" ? <p><Link href="/settings">Set your heart-rate thresholds</Link> to prepare activity analysis.</p> : null}
      {!data.analysis.running && data.analysis.ready < data.analysis.total && !data.analysis.waiting_for ? <p className="muted">Some activity analyses are unavailable. Open an activity to inspect its source file, or refresh to retry preparation.</p> : null}
      <ProgressRefresh />
    </section>
    <div className={styles.grid}>
      {data.signals.map((s) => <section className={`card ${styles.signal}`} key={s.metric}>
        <h2 className="label">{s.title}{s.band ? ` · ${s.band} grade` : ""}</h2>
        {s.latest !== null && s.baseline !== undefined ? <>
          <p className={styles.observation}>{observation(s)}</p>
          <dl className={styles.numbers}>
            <div><dt>Latest</dt><dd>{value(s.latest, s.metric)} <small>{s.unit}</small></dd></div>
            <div><dt>Earlier median</dt><dd>{value(s.baseline, s.metric)} <small>{s.unit}</small></dd></div>
          </dl>
          <p className="muted">Heart rate: {s.hr} bpm latest · {s.baseline_hr} bpm earlier median.</p>
          <details>
            <summary>View {s.evidence?.length ?? 0} activities and comparison rules</summary>
            <p className="muted">{rules[s.metric]} All comparisons use the same activity category. GPS matching allows small recording differences.</p>
            <ol className={styles.evidence}>{s.evidence?.map((a, i) => <li key={a.id}>
              <Link href={`/activities/${a.id}`}>{a.name}</Link>
              <span className="muted">{dayLabel(a.day)}{i === 0 ? " · Latest" : ""}</span>
              <strong>{value(a.value, s.metric)} {s.unit}</strong>
            </li>)}</ol>
          </details>
        </> : <>
          <p className={styles.observation}>Not enough comparable evidence</p>
          <p>{s.matches ?? 0} of 3 earlier matching days found for a recent activity.</p>
          <p className="muted">{rules[s.metric]} Requires recorded heart rate covering at least 80% of moving time.</p>
        </>}
        <p className={`muted ${styles.caveat}`}>{s.metric === "climb" ? "Similar grades can be on different trails; surface, exact slope, and technical difficulty may differ. " : ""}Weather, trail conditions, carried weight, and workout intent are not controlled. {s.metric === "durability" ? "Negative slowdown means the second half was faster. Grade adjustment is an estimate." : "Similar average heart rate does not guarantee equal effort."}</p>
      </section>)}
    </div>
  </>;
}
