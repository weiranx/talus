"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Pill from "@/components/Pill";
import { when } from "@/lib/sync";
import type { Settings } from "@/lib/types";

type Strava = Settings["sources"]["strava_web"];

export default function StravaCard({ strava }: { strava: Strava }) {
  const router = useRouter();
  // Only while a session is saved: once it's turned off, the last run's ending is history.
  const expired = strava.connected && strava.last_sync?.error_code === "session_expired";
  const [editing, setEditing] = useState(!strava.connected || expired);
  useEffect(() => { if (expired) setEditing(true); }, [expired]);
  const [cookie, setCookie] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function save() {
    setBusy(true);
    setMessage(null);
    const res = await fetch("/api/sources/strava-web", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ cookie }),
    }).catch(() => null);
    setBusy(false);
    if (!res) return setMessage({ ok: false, text: "Could not reach the engine. Is it running?" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return setMessage({ ok: false, text: data.detail ?? `Strava didn't accept it (${res.status}).` });
    setCookie("");
    setEditing(false);
    const latest = data.latest
      ? `, the latest "${data.latest.name}" on ${new Date(data.latest.start_time).toLocaleDateString("en-US", { month: "short", day: "numeric" })}`
      : "";
    setMessage({ ok: true, text: `Signed in: ${data.total} activities on Strava${latest}. Sync now pulls from it.` });
    router.refresh();
  }

  async function turnOff() {
    setBusy(true);
    await fetch("/api/sources/strava-web", { method: "DELETE" }).catch(() => null);
    setBusy(false);
    setMessage(null);
    setEditing(true);
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Strava</h2>
        <div className="actions" style={{ gap: 6 }}>
          <Pill tone="warn">Unofficial</Pill>
          {expired ? <Pill tone="bad">Session ended</Pill>
            : strava.connected && strava.enabled ? <Pill tone="good">On</Pill> : <Pill tone="neutral">Off</Pill>}
        </div>
      </div>
      <div className="muted">
        A personal fallback for when intervals.icu can&apos;t get your runs: in each sync, after intervals.icu, Talus reads
        your own activities from Strava&apos;s website with a session copied from your browser, and downloads each new
        one&apos;s original file. Strava&apos;s terms don&apos;t allow reading the site this way, so it&apos;s only for
        the owner account, at your own risk. Talus never asks for your Strava password.
      </div>
      <dl className="kv">
        <dt>Session</dt>
        <dd>{strava.connected && strava.updated_at ? `Saved ${when(strava.updated_at)}` : "Not yet"}</dd>
      </dl>
      {strava.env_override ? (
        <div className="muted">STRAVA_SESSION_COOKIE in the engine&apos;s .env is set, and it overrides the session saved here.</div>
      ) : null}
      {editing ? (
        <form className="form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          {expired ? <div className="error-text">Strava ended the session Talus held. Copy a fresh one.</div> : null}
          <ol className="muted" style={{ margin: 0, paddingLeft: "1.3em", display: "flex", flexDirection: "column", gap: 4 }}>
            <li>In Chrome, signed in to Strava, open <a href="https://www.strava.com/dashboard" target="_blank" rel="noreferrer">strava.com/dashboard</a>.</li>
            <li>Open DevTools (Cmd+Option+I on a Mac, F12 elsewhere), go to <b>Network</b> and reload the page.</li>
            <li>Click the first request, <b>dashboard</b>, then <b>Headers</b> &gt; <b>Request Headers</b>.</li>
            <li>Right-click the <b>Cookie</b> line, choose <b>Copy value</b>, and paste it below.</li>
          </ol>
          <label className="field">
            <span className="field-label">Strava Cookie header</span>
            <input type="password" autoComplete="off" spellCheck={false} required value={cookie}
              onChange={(e) => setCookie(e.target.value)} placeholder="_strava4_session=…" />
            <span className="muted">
              Checked with Strava before it&apos;s saved, then kept encrypted and never shown again. It works until Strava
              ends the session; this card says when.
            </span>
          </label>
          <div className="actions">
            <button className="btn" type="submit" disabled={busy || !cookie.trim()} aria-busy={busy}>
              {busy ? "Checking with Strava" : "Check and save"}
            </button>
            {strava.connected ? <button type="button" className="btn light outline" onClick={() => setEditing(false)}>Cancel</button> : null}
          </div>
        </form>
      ) : (
        <div className="actions">
          <button className="btn light outline" onClick={() => setEditing(true)} disabled={busy}>Replace session</button>
          <button className="link-btn" style={{ fontSize: 13 }} onClick={turnOff} disabled={busy}>Turn off</button>
        </div>
      )}
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
      <div className="muted">
        Runs another source already has are kept once. Turning it off forgets the session; activities already pulled stay.
      </div>
    </section>
  );
}
