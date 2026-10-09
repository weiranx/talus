"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Pill from "@/components/Pill";
import { dayLabel, n0 } from "@/lib/format";
import type { LastSync, Settings } from "@/lib/types";

const RANGES = [7, 30, 90, 180];

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function syncLine(s: LastSync): string {
  if (s.error) return `Failed ${when(s.at)}: ${s.error}`;
  return `${when(s.at)}: ${s.listed} activities since ${dayLabel(s.since, { month: "short", day: "numeric" })}, ${s.new} new.`;
}

export default function SourceCard({ source }: { source: Settings["source"] }) {
  const router = useRouter();
  const [days, setDays] = useState(30);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function sync() {
    setSyncing(true);
    setMessage(null);
    const res = await fetch("/api/sync", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ days }),
    }).catch(() => null);
    setSyncing(false);
    if (!res) return setMessage({ ok: false, text: "Could not reach the engine. Is it running?" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return setMessage({ ok: false, text: body.detail ?? `Sync failed (${res.status}).` });
    setMessage(body.error ? { ok: false, text: `Sync failed: ${body.error}` }
      : { ok: true, text: body.new ? `Pulled ${body.new} new ${body.new === 1 ? "activity" : "activities"}.` : "Up to date, nothing new." });
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Data source</h2>
        {source.key_set ? <Pill tone="good">Connected</Pill> : <Pill tone="bad">No API key</Pill>}
      </div>
      <dl className="kv">
        <dt>Source</dt>
        <dd>{source.name}</dd>
        <dt>API key</dt>
        <dd>{source.key_set ? "Set in .env" : <>Add <code>INTERVALS_API_KEY</code> to the engine&apos;s .env, then restart the engine</>}</dd>
        <dt>Athlete</dt>
        <dd>{source.athlete_id ? <span className="mono">{source.athlete_id}</span> : "Whoever owns the key"}</dd>
        <dt>Activities</dt>
        <dd><span className="mono">{n0(source.activities)}</span>{source.latest_activity ? `, latest ${dayLabel(source.latest_activity, { month: "short", day: "numeric", year: "numeric" })}` : ""}</dd>
        <dt>Last sync</dt>
        <dd>{source.last_sync ? syncLine(source.last_sync) : "Not from here yet"}</dd>
        {source.garmin_fallback_set ? (<><dt>Fallback</dt><dd>Garmin login set, run <code>talus sync --source garmin</code></dd></>) : null}
      </dl>
      <div className="actions">
        <label className="inline-field">
          <span className="muted">Look back</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={syncing}>
            {RANGES.map((d) => <option key={d} value={d}>{d} days</option>)}
          </select>
        </label>
        <button className="btn" onClick={sync} disabled={syncing || !source.key_set}>{syncing ? "Syncing" : "Sync now"}</button>
      </div>
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
      <div className="muted">Keys live in the engine&apos;s .env and are never sent to the browser. Only new activities are downloaded.</div>
    </section>
  );
}
