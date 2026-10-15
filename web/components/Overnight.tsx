import Link from "next/link";
import Pill from "@/components/Pill";
import type { SignalStatus, Wellness } from "@/lib/types";

const TONE: Record<SignalStatus, "good" | "warn" | "neutral"> = {
  ok: "good", low: "warn", high: "warn", short: "warn", no_data: "neutral", building: "neutral",
};
const BADGE: Record<SignalStatus, string> = {
  ok: "Normal", low: "Low", high: "High", short: "Short", no_data: "No data", building: "Building",
};

function hours(h: number | null) {
  if (h == null) return null;
  const m = Math.round(h * 60);
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

/** Overnight HRV, resting HR and sleep from the watch, each against the athlete's own baseline. */
export default function Overnight({ w }: { w: Wellness }) {
  const rows = [
    { key: "hrv", label: "HRV", value: w.hrv.last_night == null ? null : `${Math.round(w.hrv.last_night)} ms`, s: w.hrv },
    { key: "rhr", label: "Resting HR", value: w.resting_hr.last_night == null ? null : `${Math.round(w.resting_hr.last_night)} bpm`, s: w.resting_hr },
    { key: "sleep", label: "Sleep", value: hours(w.sleep.last_night_h), s: w.sleep },
  ];
  const empty = rows.every((r) => r.s.status === "no_data");
  return (
    <section className="card" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h2 className="label">Overnight</h2>
        <Link href="/health" style={{ fontSize: 14 }}>History</Link>
      </div>
      {empty && (
        <div className="muted">
          <div style={{ fontWeight: 600, color: "var(--ink)" }}>No overnight data from your watch yet</div>
          <ol style={{ margin: "6px 0 0", paddingLeft: 20 }}>
            <li>
              In intervals.icu, open{" "}
              <a href="https://intervals.icu/settings/connections" target="_blank" rel="noreferrer">Settings, Connections</a>.
            </li>
            <li>Under Garmin, tick Download wellness data, then click the second Download Old Data and OK.</li>
            <li>When the import finishes, run <span className="mono">talus sync</span> and reload this page.</li>
          </ol>
        </div>
      )}
      {rows.map((r) => (
        <div key={r.key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>
              {r.label}{" "}
              <span className="mono" style={{ fontWeight: 400 }}>{r.value ?? <span className="muted">no reading last night</span>}</span>
            </div>
            <div className="muted">{r.s.text}</div>
          </div>
          <Pill tone={TONE[r.s.status]}>{BADGE[r.s.status]}</Pill>
        </div>
      ))}
    </section>
  );
}
