"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import ConnectIntervals from "@/components/ConnectIntervals";
import Pill from "@/components/Pill";
import { dayLabel, n0 } from "@/lib/format";
import { when } from "@/lib/sync";
import type { Settings } from "@/lib/types";

// `returnTo`: where intervals.icu sends the browser back after approving (Settings by default).
// `setup`: setup imports history on its own step, so a saved key points there.
// Syncing is the Sync card's; this card connects and disconnects.
export default function SourceCard({ source, returnTo, setup }: {
  source: Settings["source"]; returnTo?: string; setup?: boolean;
}) {
  const router = useRouter();
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const oauth = source.kind === "oauth";
  // With OAuth on offer, the key form is the fallback and stays closed until asked for.
  const [editing, setEditing] = useState(!source.key_set && !source.oauth_available);
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
    setMessage({ ok: true, text: setup ? "Saved. Next, import your history." : "Saved. Sync now pulls your activities." });
    router.refresh();
  }

  async function removeKey() {
    const res = await fetch("/api/settings/source", { method: "DELETE" }).catch(() => null);
    const what = oauth ? "disconnect" : "remove the key";
    if (!res?.ok) return setMessage({ ok: false, text: res ? `Could not ${what} (${res.status}).` : "Could not reach the engine. Is it running?" });
    setMessage(null);
    setEditing(!source.oauth_available);
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">intervals.icu</h2>
        {source.key_set ? <Pill tone="good">Connected</Pill> : <Pill tone="bad">{source.oauth_available ? "Not connected" : "No API key"}</Pill>}
      </div>
      <dl className="kv">
        {oauth ? (
          <>
            <dt>Connection</dt>
            <dd>{source.updated_at ? `Approved ${when(source.updated_at)}` : "Approved"}</dd>
            <dt>Athlete</dt>
            <dd>{source.athlete_name ?? ""}{source.athlete_name && athleteId ? " " : ""}{athleteId ? <span className="mono">{athleteId}</span> : null}</dd>
          </>
        ) : (
          <>
            <dt>{source.oauth_available && !source.key_set ? "Connection" : "API key"}</dt>
            <dd>{source.key_set ? (source.updated_at ? `Saved ${when(source.updated_at)}` : "From the engine's .env") : "Not added yet"}</dd>
            {source.key_set || editing ? (
              <>
                <dt>Athlete</dt>
                <dd>{athleteId ? <span className="mono">{athleteId}</span> : "Whoever owns the key"}</dd>
              </>
            ) : null}
          </>
        )}
        <dt>In Talus</dt>
        <dd><span className="mono">{n0(source.activities)}</span>{source.latest_activity ? `, latest ${dayLabel(source.latest_activity, { month: "short", day: "numeric", year: "numeric" })}` : ""}</dd>
      </dl>
      {source.oauth_available && !oauth && !editing ? (
        <ConnectIntervals next={returnTo} hint={source.key_set ? "Replaces the API key. Talus asks to read activities and wellness only." : undefined} />
      ) : null}
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
            <button className="btn" type="submit" disabled={saving || key.trim().length < 8} aria-busy={saving}>{saving ? "Saving" : "Save key"}</button>
            {source.key_set || source.oauth_available ? <button type="button" className="btn light outline" onClick={() => setEditing(false)}>Cancel</button> : null}
          </div>
        </form>
      ) : oauth ? (
        <div className="actions">
          <button className="link-btn" style={{ fontSize: 13 }} onClick={removeKey}>Disconnect</button>
        </div>
      ) : (
        <div className="actions">
          <button className={source.oauth_available ? "link-btn" : "btn light outline"} style={source.oauth_available ? { fontSize: 13 } : undefined}
            onClick={() => setEditing(true)}>{source.key_set ? "Replace key" : "Use an API key instead"}</button>
          {source.updated_at ? <button className="link-btn" style={{ fontSize: 13 }} onClick={removeKey}>Remove key</button> : null}
        </div>
      )}
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
      <div className="muted">{oauth ? "The connection is" : "Your key is"} stored encrypted on the Talus server and never sent back to the browser. Only new activities are downloaded.</div>
    </section>
  );
}
