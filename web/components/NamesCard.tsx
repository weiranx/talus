"use client";

import { useEffect, useState } from "react";

type Status = { enabled: boolean; pending: number; estimate_usd: number; running: boolean; named: number; total: number };

// AI activity names: on or off, and a one-time pass that names past activities.
export default function NamesCard() {
  const [s, setS] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    const res = await fetch("/api/names/backfill", { cache: "no-store" }).catch(() => null);
    if (res?.ok) setS(await res.json());
  }
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!s?.running) return;
    const t = setTimeout(load, 2000);
    return () => clearTimeout(t);
  }, [s]);

  async function toggle(enabled: boolean) {
    const res = await fetch("/api/settings/names", { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled }) }).catch(() => null);
    if (!res?.ok) return setError("Could not save.");
    load();
  }

  async function backfill() {
    if (!s) return;
    const cost = s.estimate_usd < 0.01 ? "under a cent" : `about $${s.estimate_usd.toFixed(2)}`;
    if (!window.confirm(`Name ${s.pending} past ${s.pending === 1 ? "activity" : "activities"} with the coach? This costs ${cost} and runs in the background.`)) return;
    const res = await fetch("/api/names/backfill", { method: "POST" }).catch(() => null);
    if (!res?.ok) return setError("Could not start.");
    setS(await res.json());
  }

  if (!s) return null;
  return (
    <section className="card">
      <h2 className="label">Activity names</h2>
      <label className="toggle">
        <input type="checkbox" role="switch" checked={s.enabled} onChange={(e) => toggle(e.target.checked)} />
        <span>
          <span style={{ fontWeight: 600 }}>Let the coach name activities</span>
          <span className="muted" style={{ display: "block" }}>
            Replaces names like &ldquo;Morning Run&rdquo; with the peak or place and the kind of day. A name you typed, here or on your watch, is kept.
          </span>
        </span>
      </label>
      {s.running ? (
        <div className="muted">Naming past activities: {s.named} of {s.total}.</div>
      ) : s.enabled && s.pending > 0 ? (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn light outline" onClick={backfill}>Name {s.pending} past {s.pending === 1 ? "activity" : "activities"}</button>
          <span className="muted" style={{ fontSize: 13 }}>{s.estimate_usd < 0.01 ? "Under a cent" : `About $${s.estimate_usd.toFixed(2)}`}</span>
        </div>
      ) : s.enabled ? <div className="muted">Every activity with a generic name has a coach&apos;s name.</div> : null}
      {error ? <span className="error-text">{error}</span> : null}
    </section>
  );
}
