"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { addedLine, RANGES, stravaLine, syncAll, syncLine, when, type Added } from "@/lib/sync";
import type { Settings, SyncResult } from "@/lib/types";

type Row = { key: string; label: string; line: string; bad: boolean };

// One row per source that syncs: the ones Talus pulls from here, then the watch, which only the
// phone or a browser next to it can reach.
function rows(src: Settings["sources"]): { pulled: Row[]; watch: Row | null } {
  const row = (key: string, label: string, run: { error: string | null } | null, line: string | null): Row =>
    ({ key, label, line: line ?? "Ready for first sync", bad: Boolean(run?.error) });
  const pulled: Row[] = [];
  const { intervals, garmin, coros, strava_web: strava, watch: w } = src;
  if (intervals.connected) pulled.push(row("intervals", "intervals.icu", intervals.last_sync, intervals.last_sync && syncLine(intervals.last_sync)));
  if (garmin.available && garmin.connected) pulled.push(row("garmin", "Garmin Connect", garmin.last_sync, garmin.last_sync && syncLine(garmin.last_sync)));
  if (coros.available && coros.connected) pulled.push(row("coros", "COROS", coros.last_sync, coros.last_sync && syncLine(coros.last_sync)));
  if (strava?.available && strava.enabled) pulled.push(row("strava_web", "Strava", strava.last_sync, strava.last_sync && stravaLine(strava.last_sync)));
  if (!w.enabled) return { pulled, watch: null };
  // A failed attempt newer than the last good sync is what the watch row shows.
  const failed = w.last_attempt?.error && w.last_attempt.at > (w.last_sync ?? "") ? w.last_attempt : null;
  const where = "Sync separately from the iPhone app or the watch card below.";
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
  const [latest, setLatest] = useState<SyncResult | null>(null);
  const { pulled, watch } = rows(sources);
  const sourceNames = pulled.map((r) => r.label).join(", ").replace(/, ([^,]*)$/, " and $1");
  const visibleRows = pulled.map((row) => {
    const run = latest?.sources.find((s) => s.source === row.key);
    return run ? { ...row, line: row.key === "strava_web" ? stravaLine(run) : syncLine(run), bad: Boolean(run.error) } : row;
  });

  async function sync() {
    setBusy(true);
    setMessage(null);
    setAdded([]);
    try {
      const { result, ...msg } = await syncAll(days);
      setMessage(msg);
      setLatest(result);
      setAdded(result?.added ?? []);
      router.refresh();
    } catch {
      setMessage({ ok: false, text: "Sync couldn't finish. Try again; activities already added are safe." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card sync-card" aria-labelledby="sync-title">
      <h2 className="label" id="sync-title">Sync activities</h2>
      <div className="muted">{pulled.length
        ? `Bring your latest activities into Talus from ${sourceNames}.`
        : watch ? "Your Garmin watch syncs from a nearby device." : "Connect a source below to get started, or upload activity files."}</div>
      {pulled.length ? (
        <>
          <div className="actions">
            <button className="btn" onClick={sync} disabled={busy} aria-busy={busy}>{busy ? "Syncing…" : message && !message.ok ? "Try sync again" : "Sync now"}</button>
            <span className="muted">Last {days} days · duplicates skipped</span>
          </div>
          <details className="sync-options">
            <summary>Import older activities</summary>
            <label className="inline-field">
              <span>Check the last</span>
              <select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={busy}>
                {RANGES.map((d) => <option key={d} value={d}>{d} days</option>)}
              </select>
            </label>
            <div className="muted">Choose a range, then sync. Activities already in Talus are kept once.</div>
          </details>
        </>
      ) : null}
      <div role="status" aria-live="polite" aria-atomic="true">
        {busy ? <div className="sync-feedback"><strong>Checking connected sources…</strong><div className="muted">Keep this page open. A first sync or a longer history can take a few minutes.</div></div>
          : message ? <div className={`sync-feedback ${message.ok ? "ok-text" : "error-text"}`}>{message.text}</div> : null}
      </div>
      {visibleRows.length ? <ul className="sync-sources" aria-label="Connected sources">
        {visibleRows.map((r) => <li key={r.key}><strong>{r.label}</strong><span className={r.bad ? "error-text" : "muted"}>{r.line}</span></li>)}
      </ul> : null}
      {added.length ? (
        <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
          {added.map((a) => <li key={a.id}><a href={`/activities/${a.id}`}>{addedLine(a)}</a></li>)}
        </ul>
      ) : null}
      {watch ? <div className="sync-feedback"><strong>Garmin watch · separate sync</strong><div className={watch.bad ? "error-text" : "muted"}>{watch.line}</div><a href="#garmin-watch">Go to watch sync ↓</a></div> : null}
    </section>
  );
}
