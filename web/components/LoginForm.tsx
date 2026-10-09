"use client";

import { useEffect, useState } from "react";

interface Status { account: { email: string } | null; has_accounts: boolean; signup_open: boolean; existing_data: boolean; google: boolean }

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
    const googleError = new URLSearchParams(window.location.search).get("google_error");
    if (googleError) setError(googleError);
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
      {status?.google && (status.signup_open || mode === "login") ? (
        <>
          <a className="btn light outline google-btn" href={`/api/auth/google/start?next=${encodeURIComponent(nextPath())}`}>
            <GoogleMark /> Continue with Google
          </a>
          <div className="or-line"><span>or with email</span></div>
        </>
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

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.2C12.4 13.6 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 2.9-2.2 5.4-4.7 7.1l7.6 5.9c4.4-4.1 6.9-10.1 6.9-17.5z" />
      <path fill="#FBBC05" d="M10.6 28.5c-.5-1.4-.8-2.9-.8-4.5s.3-3.1.8-4.5l-7.9-6.2C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.2z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.9 2.3-8.3 2.3-6.3 0-11.6-4.1-13.4-9.9l-7.9 6.2C6.6 42.6 14.6 48 24 48z" />
    </svg>
  );
}
