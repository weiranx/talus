"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Spinner } from "@/components/Loading";
import { n0 } from "@/lib/format";
import type { ImportRange, Onboarding } from "@/lib/types";

const RANGES: { key: ImportRange; label: string; hint: string }[] = [
  { key: "6w", label: "6 weeks", hint: "Quickest. Your normal takes a few more weeks to settle." },
  { key: "6m", label: "6 months", hint: "Recommended. Enough for your normal and for estimating heart rate thresholds." },
  { key: "all", label: "Everything", hint: "Up to 2 years. Slowest, and the most history for plans." },
];

// Pull history from the connected source in the background, with progress, so the next steps
// can be answered while it runs.
export default function ImportStep({ ob }: { ob: Onboarding }) {
  const router = useRouter();
  const [range, setRange] = useState<ImportRange>(ob.import?.range ?? "6m");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const imp = ob.import;
  const running = imp?.state === "running";
  const source = ob.sources.intervals ? "intervals" : ob.sources.garmin ? "garmin" : ob.sources.coros ? "coros" : null;

  // While it runs, refresh the page every couple of seconds for the progress.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => router.refresh(), 2000);
    return () => clearInterval(t);
  }, [running, router]);

  async function start() {
    setStarting(true);
    setError(null);
    const res = await fetch("/api/onboarding/import", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ source, range }),
    }).catch(() => null);
    setStarting(false);
    if (!res) return setError("Could not reach the engine. Is it running?");
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return setError(typeof body.detail === "string" ? body.detail : `Could not start the import (${res.status}).`);
    }
    router.refresh();
  }

  if (!source) {
    return (
      <section className="card">
        <h2 className="label">Your history</h2>
        {ob.activities ? (
          <div className="ok-text">{n0(ob.activities)} activities are in from your uploads. Upload more any time in Settings.</div>
        ) : (
          <div className="muted">Upload files on the Connect step, or connect intervals.icu, to bring in your history.</div>
        )}
      </section>
    );
  }

  const pct = imp?.total ? Math.round((imp.done / imp.total) * 100) : 0;
  return (
    <section className="card">
      <h2 className="label">How far back</h2>
      <div className="muted">
        Talus reads your runs and hikes from {source === "intervals" ? "intervals.icu" : "Garmin Connect"} and scores each one
        for distance, climb, descent and heart rate. It also brings in resting heart rate, HRV and sleep for the last 60 nights.
      </div>
      <div role="radiogroup" aria-label="How far back" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {RANGES.map((r) => (
          <label key={r.key} className="toggle" style={{ alignItems: "flex-start" }}>
            <input type="radio" name="range" value={r.key} checked={range === r.key} disabled={running}
              onChange={() => setRange(r.key)} />
            <span>
              <span style={{ fontWeight: 600 }}>{r.label}</span>
              <span className="muted" style={{ display: "block" }}>{r.hint}</span>
            </span>
          </label>
        ))}
      </div>
      <div className="actions">
        <button className="btn" onClick={start} disabled={running || starting} aria-busy={starting || running}>
          {running ? "Importing" : imp?.state === "done" ? "Import again" : "Start import"}
        </button>
      </div>
      {running ? (
        <div className="inferred">
          <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}><Spinner /> Importing your history</div>
          <div className="progress" role="progressbar" aria-label="Activities imported" aria-valuemin={0}
            aria-valuemax={imp.total ?? 0} aria-valuenow={imp.done}>
            <span style={{ width: `${pct}%` }} />
          </div>
          <div className="muted">
            {imp.total == null ? "Asking for the list of activities." : `${imp.done} of ${imp.total} new activities.`} This keeps
            going if you move on to the next steps.
          </div>
        </div>
      ) : null}
      {imp?.state === "done" && !imp.error ? (
        <div className="ok-text" role="status">
          Imported {imp.saved} {imp.saved === 1 ? "activity" : "activities"} from the last {imp.days} days
          {imp.duplicates ? `, ${imp.duplicates} already here from another source` : ""}
          {imp.wellness_days ? `, and ${imp.wellness_days} nights of sleep and heart rate` : ""}.
        </div>
      ) : null}
      {imp?.error ? <div className="error-text" role="status">The import stopped: {imp.error}. Check the connection on the Connect step, then start it again.</div> : null}
      {error ? <div className="error-text" role="status">{error}</div> : null}
    </section>
  );
}
