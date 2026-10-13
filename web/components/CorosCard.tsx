"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Pill from "@/components/Pill";
import { RANGES, runSync, syncLine, when } from "@/components/SourceCard";
import type { Settings } from "@/lib/types";

type Coros = Settings["sources"]["coros"];

const REGIONS: [string, string][] = [["en", "Americas, Asia-Pacific and the rest"], ["eu", "Europe"], ["cn", "China"]];

export default function CorosCard({ coros }: { coros: Coros }) {
  const router = useRouter();
  const [login, setLogin] = useState(!coros.connected);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [region, setRegion] = useState(coros.region ?? "en");
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState(30);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function connect() {
    setBusy(true);
    setMessage(null);
    const res = await fetch("/api/sources/coros", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: email.trim(), password, region }),
    }).catch(() => null);
    setBusy(false);
    if (!res) return setMessage({ ok: false, text: "Could not reach the engine. Is it running?" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return setMessage({ ok: false, text: data.detail ?? `COROS sign-in failed (${res.status}).` });
    setPassword("");
    setLogin(false);
    setMessage({ ok: true, text: "Connected. Sync to pull your activities." });
    router.refresh();
  }

  async function disconnect() {
    await fetch("/api/sources/coros", { method: "DELETE" }).catch(() => null);
    setLogin(true);
    router.refresh();
  }

  async function sync() {
    setBusy(true);
    setMessage(null);
    const result = await runSync("coros", days);
    setBusy(false);
    setMessage(result);
    if (!result.ok && result.text.includes("Sign in to COROS again")) setLogin(true);
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">COROS</h2>
        <div className="actions" style={{ gap: 6 }}>
          <Pill tone="warn">Unofficial</Pill>
          {coros.connected ? <Pill tone="good">Connected</Pill> : <Pill tone="neutral">Not connected</Pill>}
        </div>
      </div>
      <div className="muted">
        Pulls activities (FIT files), overnight HRV and resting heart rate straight from COROS. COROS opens its API only
        to approved partners, so this signs in the way the Training Hub website does. COROS keeps one web sign-in per
        account: signing in to Training Hub in a browser signs Talus out, and this card asks for your password again.
        Use at your own risk.
      </div>
      <dl className="kv">
        <dt>Sign-in</dt>
        <dd>{coros.connected && coros.updated_at ? `Saved ${when(coros.updated_at)}` : "Not yet"}</dd>
        <dt>Last sync</dt>
        <dd>{coros.last_sync ? syncLine(coros.last_sync) : "Not yet"}</dd>
      </dl>
      {login ? (
        <form className="form" onSubmit={(e) => { e.preventDefault(); connect(); }}>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">COROS email</span>
              <input type="email" autoComplete="off" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">COROS password</span>
              <input type="password" autoComplete="off" required value={password} onChange={(e) => setPassword(e.target.value)} />
              <span className="muted">Used once to sign in, never stored.</span>
            </label>
            <label className="field">
              <span className="field-label">Account region</span>
              <select value={region} onChange={(e) => setRegion(e.target.value)}>
                {REGIONS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select>
            </label>
          </div>
          <div className="actions">
            <button className="btn" type="submit" disabled={busy || !email || !password} aria-busy={busy}>{busy ? "Signing in" : "Connect COROS"}</button>
            {coros.connected ? <button type="button" className="btn light outline" onClick={() => setLogin(false)}>Cancel</button> : null}
          </div>
        </form>
      ) : (
        <>
          <div className="actions">
            <label className="inline-field">
              <span className="muted">Look back</span>
              <select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={busy}>
                {RANGES.map((d) => <option key={d} value={d}>{d} days</option>)}
              </select>
            </label>
            <button className="btn" onClick={sync} disabled={busy} aria-busy={busy}>{busy ? "Syncing" : "Sync now"}</button>
          </div>
          <div className="actions">
            <button className="btn light outline" onClick={() => setLogin(true)}>Sign in again</button>
            <button className="link-btn" style={{ fontSize: 13 }} onClick={disconnect}>Disconnect</button>
          </div>
        </>
      )}
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
      <div className="muted">
        Runs another source already has are kept once. Sleep time isn&apos;t in COROS&apos;s data here; it still comes
        from intervals.icu or Apple Health.
      </div>
    </section>
  );
}
