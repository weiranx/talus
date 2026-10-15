import Link from "next/link";
import EngineDown from "@/components/EngineDown";
import HealthChart from "@/components/HealthChart";
import LinkPending from "@/components/LinkPending";
import Pill from "@/components/Pill";
import { engine, engineFailed } from "@/lib/api";
import { dayLabel } from "@/lib/format";
import type { HealthHistory, SignalStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

const RANGES = { "30d": { days: 30, label: "30 d" }, "90d": { days: 90, label: "90 d" }, "6m": { days: 180, label: "6 mo" }, "1y": { days: 365, label: "1 yr" } } as const;

const SOURCE: Record<string, string> = {
  watch: "Watch", apple_health: "Apple Health", healthkit: "Apple Health", intervals: "intervals.icu",
  garmin: "Garmin Connect", coros: "COROS",
};

function hours(h: number | null) {
  if (h == null) return null;
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

/** A value, tinted when that morning's read flagged it (low HRV, high resting HR, short sleep). */
function Cell({ v, status }: { v: string | null; status: SignalStatus }) {
  if (v == null) return <td className="mono muted">–</td>;
  const flagged = status === "low" || status === "high" || status === "short";
  return <td className="mono" style={flagged ? { color: "var(--warn-ink)", fontWeight: 600 } : undefined}>{v}</td>;
}

export default async function HealthPage({ searchParams }: { searchParams: Promise<{ range?: string }> }) {
  const { range: r } = await searchParams;
  const range = (r && r in RANGES ? r : "90d") as keyof typeof RANGES;
  let data: HealthHistory;
  try {
    data = await engine<HealthHistory>(`/api/wellness/history?days=${RANGES[range].days}`);
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }
  const { nights, average: avg } = data;
  const cards = [
    { metric: "hrv" as const, title: "HRV", value: avg.hrv == null ? null : `${Math.round(avg.hrv)} ms`,
      note: "Dots: each night. Solid: 7-day average. Dashed: 60-day baseline. Low reads 8% or more under it." },
    { metric: "resting_hr" as const, title: "Resting HR", value: avg.resting_hr == null ? null : `${Math.round(avg.resting_hr)} bpm`,
      note: "Solid: each night. Dashed: 60-day baseline. High reads 5 bpm or more over it." },
    { metric: "sleep_h" as const, title: "Sleep", value: hours(avg.sleep_h),
      note: `Time asleep each night, not time in bed. Dashed: your ${hours(data.need_h)} need.` },
  ];

  return (
    <>
      <header className="page-head">
        <div style={{ flex: "1 1 320px" }}>
          <h1 className="title">Health</h1>
          <div className="muted">
            Overnight HRV, resting HR and sleep, each night filed under the morning you woke up. Flagged values are
            the ones Today read as low, high or short that morning.
          </div>
        </div>
        <nav className="seg-tabs" aria-label="Time range">
          {Object.entries(RANGES).map(([k, v]) => (
            <Link key={k} href={`/health?range=${k}`} aria-current={k === range ? "true" : undefined}>{v.label}<LinkPending /></Link>
          ))}
        </nav>
      </header>

      {!nights.length ? (
        <section className="card">
          <div style={{ fontWeight: 600 }}>No overnight data in the last {RANGES[range].label}</div>
          <div className="muted">
            Turn on the watch or Apple Health under <Link href="/settings">Settings, Data sources</Link> and sync, or
            tick Download wellness data for Garmin in intervals.icu.
          </div>
        </section>
      ) : (
        <>
          {cards.map((c) => (
            <section key={c.metric} className="card" style={{ gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                <h2 style={{ fontSize: 17, fontWeight: 600 }}>{c.title}</h2>
                <div><span className="mono" style={{ fontWeight: 600 }}>{c.value ?? "–"}</span> <span className="muted">average</span></div>
              </div>
              <HealthChart nights={nights} metric={c.metric} needH={data.need_h} />
              <div className="muted" style={{ fontSize: 13 }}>{c.note}</div>
            </section>
          ))}

          <section className="card" style={{ gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <h2 className="label">Each night</h2>
              <Pill tone="neutral">{nights.length} {nights.length === 1 ? "night" : "nights"}</Pill>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Morning</th><th>HRV</th><th>Resting HR</th><th>Sleep</th><th>From</th></tr>
                </thead>
                <tbody>
                  {nights.map((n) => (
                    <tr key={n.day}>
                      <td>{dayLabel(n.day, { weekday: "short", month: "short", day: "numeric" })}</td>
                      <Cell v={n.hrv == null ? null : `${Math.round(n.hrv)} ms`} status={n.hrv_status} />
                      <Cell v={n.resting_hr == null ? null : `${Math.round(n.resting_hr)} bpm`} status={n.resting_hr_status} />
                      <Cell v={hours(n.sleep_h)} status={n.sleep_status} />
                      <td className="muted">{n.source ? SOURCE[n.source] ?? n.source : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
