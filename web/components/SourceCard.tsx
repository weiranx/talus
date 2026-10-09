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
  const [editing, setEditing] = useState(!source.key_set);
  const [key, setKey] = useState("");
  // "0" means whoever owns the key, the same as leaving the field blank.
  const athleteId = source.athlete_id && source.athlete_id !== "0" ? source.athlete_id : null;
  const [athlete, setAthlete] = useState(athleteId ?? "");
  const [saving, setSaving] = useState(false);

  async function saveKey(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    const res = await fetch("/api/settings/source", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ api_key: key.trim(), athlete_id: athlete.trim() || "0" }),
    }).catch(() => null);
    setSaving(false);
    if (!res) return setMessage({ ok: false, text: "Could not reach the engine. Is it running?" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return setMessage({ ok: false, text: res.status === 422 ? "Check the key, and that the athlete id looks like i123456."
        : typeof body.detail === "string" ? body.detail : `Could not save the key (${res.status}).` });
    }
    setKey("");
    setEditing(false);
    setMessage({ ok: true, text: "Saved. Sync to pull your activities." });
    router.refresh();
  }

  async function removeKey() {
    const res = await fetch("/api/settings/source", { method: "DELETE" }).catch(() => null);
    if (!res?.ok) return setMessage({ ok: false, text: res ? `Could not remove the key (${res.status}).` : "Could not reach the engine. Is it running?" });
    setEditing(true);
    router.refresh();
  }

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
      : { ok: true, text: body.saved ? `Pulled ${body.saved} new ${body.saved === 1 ? "activity" : "activities"}.` : "Up to date, nothing new." });
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
        <dd>{source.key_set ? (source.updated_at ? `Saved ${when(source.updated_at)}` : "From the engine's .env") : "Not added yet"}</dd>
        <dt>Athlete</dt>
        <dd>{athleteId ? <span className="mono">{athleteId}</span> : "Whoever owns the key"}</dd>
        <dt>Activities</dt>
        <dd><span className="mono">{n0(source.activities)}</span>{source.latest_activity ? `, latest ${dayLabel(source.latest_activity, { month: "short", day: "numeric", year: "numeric" })}` : ""}</dd>
        <dt>Last sync</dt>
        <dd>{source.last_sync ? syncLine(source.last_sync) : "Not from here yet"}</dd>
        {source.garmin_fallback_set ? (<><dt>Fallback</dt><dd>Garmin login set, run <code>talus sync --source garmin</code></dd></>) : null}
      </dl>
      {editing ? (
        <form className="form" onSubmit={saveKey}>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">intervals.icu API key</span>
              <input type="password" autoComplete="off" required minLength={8} value={key} onChange={(e) => setKey(e.target.value)} />
              <span className="muted">intervals.icu, Settings, Developer Settings.</span>
            </label>
            <label className="field">
              <span className="field-label">Athlete id</span>
              <input value={athlete} placeholder="i123456" pattern="0|i?[0-9]{1,12}" onChange={(e) => setAthlete(e.target.value)} />
              <span className="muted">Next to the key. Blank means whoever owns the key.</span>
            </label>
          </div>
          <div className="actions">
            <button className="btn" type="submit" disabled={saving || key.trim().length < 8}>{saving ? "Saving" : "Save key"}</button>
            {source.key_set ? <button type="button" className="btn light outline" onClick={() => setEditing(false)}>Cancel</button> : null}
          </div>
        </form>
      ) : (
        <div className="actions">
          <button className="btn light outline" onClick={() => setEditing(true)}>Replace key</button>
          {source.updated_at ? <button className="link-btn" style={{ fontSize: 13 }} onClick={removeKey}>Remove key</button> : null}
        </div>
      )}
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
      <div className="muted">Your key is stored encrypted on the Talus server and never sent back to the browser. Only new activities are downloaded.</div>
    </section>
  );
}
