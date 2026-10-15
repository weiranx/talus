"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Pill from "@/components/Pill";
import { when } from "@/lib/sync";
import type { Settings } from "@/lib/types";

type Garmin = Settings["sources"]["garmin"];

export default function GarminCard({ garmin }: { garmin: Garmin }) {
  const router = useRouter();
  const [step, setStep] = useState<"idle" | "login" | "mfa">(garmin.mfa_pending ? "mfa" : garmin.connected ? "idle" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function post(path: string, body: object) {
    setBusy(true);
    setMessage(null);
    const res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
    setBusy(false);
    if (!res) return setMessage({ ok: false, text: "Could not reach the engine. Is it running?" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return setMessage({ ok: false, text: data.detail ?? `Garmin sign-in failed (${res.status}).` });
    setPassword("");
    setCode("");
    if (data.status === "needs_mfa") {
      setStep("mfa");
      return setMessage({ ok: true, text: "Garmin sent a code to your email or phone. Enter it here." });
    }
    setStep("idle");
    setMessage({ ok: true, text: "Connected. Sync now pulls your activities." });
    router.refresh();
  }

  async function disconnect() {
    await fetch("/api/sources/garmin", { method: "DELETE" }).catch(() => null);
    setStep("login");
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Garmin Connect</h2>
        <div className="actions" style={{ gap: 6 }}>
          <Pill tone="warn">Unofficial</Pill>
          {garmin.connected ? <Pill tone="good">Connected</Pill> : <Pill tone="neutral">Not connected</Pill>}
        </div>
      </div>
      <div className="muted">
        Pulls activities, HRV, resting heart rate and sleep straight from Garmin, without intervals.icu. Garmin has no
        public API for new apps, so this signs in the way the Garmin Connect website does. Garmin can break it at any
        time or limit an account that syncs often. Use at your own risk.
      </div>
      <dl className="kv">
        <dt>Sign-in</dt>
        <dd>{garmin.connected && garmin.updated_at ? `Saved ${when(garmin.updated_at)}, good for about 6 months` : "Not yet"}</dd>
      </dl>
      {step === "login" ? (
        <form className="form" onSubmit={(e) => { e.preventDefault(); post("/api/sources/garmin", { email: email.trim(), password }); }}>
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Garmin email</span>
              <input type="email" autoComplete="off" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">Garmin password</span>
              <input type="password" autoComplete="off" required value={password} onChange={(e) => setPassword(e.target.value)} />
              <span className="muted">Used once to sign in, never stored.</span>
            </label>
          </div>
          <div className="actions">
            <button className="btn" type="submit" disabled={busy || !email || !password} aria-busy={busy}>{busy ? "Signing in" : "Connect Garmin"}</button>
            {garmin.connected ? <button type="button" className="btn light outline" onClick={() => setStep("idle")}>Cancel</button> : null}
          </div>
        </form>
      ) : step === "mfa" ? (
        <form className="form" onSubmit={(e) => { e.preventDefault(); post("/api/sources/garmin/mfa", { code: code.trim() }); }}>
          <label className="field" style={{ maxWidth: 220 }}>
            <span className="field-label">Garmin security code</span>
            <input inputMode="numeric" autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <div className="actions">
            <button className="btn" type="submit" disabled={busy || code.trim().length < 4} aria-busy={busy}>{busy ? "Checking" : "Finish"}</button>
            <button type="button" className="link-btn" style={{ fontSize: 13 }} onClick={() => setStep("login")}>Start over</button>
          </div>
        </form>
      ) : (
        <div className="actions">
          <button className="btn light outline" onClick={() => setStep("login")}>Sign in again</button>
          <button className="link-btn" style={{ fontSize: 13 }} onClick={disconnect}>Disconnect</button>
        </div>
      )}
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
      <div className="muted">Runs that intervals.icu also has are recognized and kept once.</div>
    </section>
  );
}
