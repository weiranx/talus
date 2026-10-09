"use client";

import { useEffect, useState } from "react";

interface Status { account: { email: string } | null; has_accounts: boolean; signup_open: boolean; existing_data: boolean }

// Only same-site paths, so a crafted ?next= can't send someone elsewhere after signing in.
function nextPath(): string {
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export default function LoginForm() {
  const [status, setStatus] = useState<Status | null>(null);
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((s: Status) => {
        if (s.account) return window.location.replace(nextPath());
        setStatus(s);
        if (!s.has_accounts) setMode("signup");
      })
      .catch(() => setError("Could not reach the engine. Start it with: cd engine && uv run talus serve"));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch(`/api/auth/${mode}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    }).catch(() => null);
    setBusy(false);
    if (!res) return setError("Could not reach the engine. Is it running?");
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return setError(typeof body.detail === "string" ? body.detail : "Check your email and password.");
    }
    window.location.replace(nextPath());
  }

  const first = status && !status.has_accounts;
  return (
    <section className="card">
      <h1 className="title" style={{ fontSize: 26 }}>{mode === "signup" ? (first ? "Set up Talus" : "Create an account") : "Sign in"}</h1>
      {first && status.existing_data ? (
        <div className="muted">
          Your existing activities, thresholds and coach history are waiting. The account you create now owns them,
          and a backup of the old database is in <code>data/backups</code>.
        </div>
      ) : first ? (
        <div className="muted">The first account owns this Talus: it can sync from the engine&apos;s .env key and has no coach spending cap.</div>
      ) : null}
      <form className="form" onSubmit={submit}>
        <label className="field">
          <span className="field-label">Email</span>
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input type="password" autoComplete={mode === "signup" ? "new-password" : "current-password"} required
            minLength={mode === "signup" ? 10 : undefined} value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "signup" ? <span className="muted">At least 10 characters.</span> : null}
        </label>
        <div className="actions">
          <button className="btn" type="submit" disabled={busy || !status}>
            {busy ? "One moment" : mode === "signup" ? "Create account" : "Sign in"}
          </button>
          {status && status.has_accounts && status.signup_open ? (
            <button type="button" className="btn light outline" onClick={() => { setMode(mode === "login" ? "signup" : "login"); setError(null); }}>
              {mode === "login" ? "Create an account" : "I have an account"}
            </button>
          ) : null}
        </div>
        {error ? <div className="error-text" role="alert">{error}</div> : null}
      </form>
    </section>
  );
}
