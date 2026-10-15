"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { addedLine, RANGES, stravaLine, syncAll, syncLine, when, type Added } from "@/lib/sync";
import type { Settings } from "@/lib/types";

type Row = { key: string; label: string; line: string; bad: boolean };

// One row per source that syncs: the ones Talus pulls from here, then the watch, which only the
// phone or a browser next to it can reach.
function rows(src: Settings["sources"]): { pulled: Row[]; watch: Row | null } {
  const row = (key: string, label: string, run: { error: string | null } | null, line: string | null): Row =>
    ({ key, label, line: line ?? "Not yet", bad: Boolean(run?.error) });
  const pulled: Row[] = [];
  const { intervals, garmin, coros, strava_web: strava, watch: w } = src;
  if (intervals.connected) pulled.push(row("intervals", "intervals.icu", intervals.last_sync, intervals.last_sync && syncLine(intervals.last_sync)));
  if (garmin.available && garmin.connected) pulled.push(row("garmin", "Garmin Connect", garmin.last_sync, garmin.last_sync && syncLine(garmin.last_sync)));
  if (coros.available && coros.connected) pulled.push(row("coros", "COROS", coros.last_sync, coros.last_sync && syncLine(coros.last_sync)));
  if (strava?.available && strava.enabled) pulled.push(row("strava_web", "Strava", strava.last_sync, strava.last_sync && stravaLine(strava.last_sync)));
  if (!w.enabled) return { pulled, watch: null };
  // A failed attempt newer than the last good sync is what the watch row shows.
  const failed = w.last_attempt?.error && w.last_attempt.at > (w.last_sync ?? "") ? w.last_attempt : null;
  const where = "From the iPhone app, or the watch card below.";
  return {
    pulled,
    watch: row("watch", "Garmin watch", failed, failed ? `Failed ${when(failed.at)}: ${failed.error}`
      : `${w.last_sync ? when(w.last_sync) : "Not yet"}. ${where}`),
  };
}

export default function SyncCard({ sources }: { sources: Settings["sources"] }) {
  const router = useRouter();
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [added, setAdded] = useState<Added[]>([]);
  const { pulled, watch } = rows(sources);

  async function sync() {
    setBusy(true);
    setMessage(null);
    setAdded([]);
    const { result, ...msg } = await syncAll(days);
    setMessage(msg);
    setAdded(result?.added ?? []);
    setBusy(false);
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">Sync</h2>
      {pulled.length || watch ? (
        <dl className="kv">
          {[...pulled, ...(watch ? [watch] : [])].map((r) => (
            <div key={r.key} style={{ display: "contents" }}>
              <dt>{r.label}</dt>
              <dd className={r.bad ? "error-text" : undefined}>{r.line}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className="muted">Nothing to sync from yet. Connect a source below, or upload files.</div>
      )}
      {pulled.length ? (
        <div className="actions">
          <label className="inline-field">
            <span className="muted">Look back</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={busy}>
              {RANGES.map((d) => <option key={d} value={d}>{d} days</option>)}
            </select>
          </label>
          <button className="btn" onClick={sync} disabled={busy} aria-busy={busy}>{busy ? "Syncing" : "Sync now"}</button>
        </div>
      ) : null}
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
      {added.length ? (
        <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
          {added.map((a) => <li key={a.id}><a href={`/activities/${a.id}`}>{addedLine(a)}</a></li>)}
        </ul>
      ) : null}
      <div className="muted">
        {pulled.length > 1 ? `Sync now pulls from ${pulled.map((r) => r.label).join(", ").replace(/, ([^,]*)$/, " and $1")}, one after another. ` : ""}
        Only new activities are downloaded, and a run two sources both have is kept once.
      </div>
    </section>
  );
}
