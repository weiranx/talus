"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Account } from "@/lib/types";

type Msg = { ok: boolean; text: string } | null;

async function send(path: string, method: string, body: object): Promise<{ ok: boolean; detail?: string }> {
  const res = await fetch(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res) return { ok: false, detail: "Could not reach the engine. Is it running?" };
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, detail: typeof data.detail === "string" ? data.detail : undefined };
}

export default function AccountCard({ account }: { account: Account }) {
  const [pw, setPw] = useState({ current: "", new: "" });
  const [pwMsg, setPwMsg] = useState<Msg>(null);
  const [confirming, setConfirming] = useState(false);
  const [delPw, setDelPw] = useState("");
  const [delMsg, setDelMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState<"google" | "password" | "delete" | null>(null);
  const [googleMsg, setGoogleMsg] = useState<Msg>(null);
  const router = useRouter();

  useEffect(() => {
    const err = new URLSearchParams(window.location.search).get("google_error");
    if (err) setGoogleMsg({ ok: false, text: err });
  }, []);

  async function unlinkGoogle() {
    setBusy("google");
    const r = await fetch("/api/account/google/unlink", { method: "POST" }).catch(() => null);
    setBusy(null);
    const body = await r?.json().catch(() => ({}));
    if (!r?.ok) return setGoogleMsg({ ok: false, text: body?.detail ?? "Could not unlink Google." });
    setGoogleMsg({ ok: true, text: "Google unlinked. Sign in with your email and password." });
    router.refresh();
  }

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy("password");
    const r = await send("/api/account/password", "PUT", pw);
    setBusy(null);
    setPwMsg(r.ok ? { ok: true, text: "Password changed. Other devices are signed out." } : { ok: false, text: r.detail ?? "Could not change it." });
    if (r.ok) {
      setPw({ current: "", new: "" });
      router.refresh();
    }
  }

  async function remove(e: React.FormEvent) {
    e.preventDefault();
    setBusy("delete");
    const r = await send("/api/account/delete", "POST", { password: delPw });
    setBusy(null);
    if (r.ok) return window.location.replace("/login");
    setDelMsg({ ok: false, text: r.detail ?? "Could not delete the account." });
  }

  return (
    <section className="card">
      <h2 className="label">Account</h2>
      <dl className="kv">
        <dt>Email</dt>
        <dd>{account.email}{account.is_owner ? " (owner)" : ""}</dd>
        <dt>Since</dt>
        <dd>{new Date(account.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</dd>
      </dl>

      {account.google_available || account.google ? (
        <div className="actions">
          <span>
            {account.google ? <>Google: <span style={{ overflowWrap: "anywhere" }}>{account.google.email}</span></> : "Google not linked"}
          </span>
          {account.google ? (
            <button className="btn light outline" onClick={unlinkGoogle} disabled={!!busy || !account.has_password} aria-busy={busy === "google"}
              title={account.has_password ? undefined : "Set a password first"}>Unlink Google</button>
          ) : (
            <a className="btn light outline google-btn" href="/api/auth/google/start?link=1&next=/settings">Link Google</a>
          )}
        </div>
      ) : null}
      {googleMsg ? <div className={googleMsg.ok ? "ok-text" : "error-text"} role="status">{googleMsg.text}</div> : null}

      <form className="form" onSubmit={changePassword}>
        {account.has_password ? null : <div className="muted">You sign in with Google. Set a password to also sign in with email.</div>}
        <div className="form-grid">
          {account.has_password ? (
            <label className="field">
              <span className="field-label">Current password</span>
              <input type="password" autoComplete="current-password" required value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            </label>
          ) : null}
          <label className="field">
            <span className="field-label">New password</span>
            <input type="password" autoComplete="new-password" required minLength={10} value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} />
          </label>
        </div>
        <div className="actions">
          <button className="btn light outline" type="submit" disabled={!!busy || (account.has_password && !pw.current) || pw.new.length < 10} aria-busy={busy === "password"}>{account.has_password ? "Change password" : "Set password"}</button>
        </div>
        {pwMsg ? <div className={pwMsg.ok ? "ok-text" : "error-text"} role="status">{pwMsg.text}</div> : null}
      </form>

      <div className="divider" />
      <div className="actions">
        <a className="btn light outline" href="/api/account/export" style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}>Export my data</a>
        <span className="muted">A zip of your activities, raw files, settings and coach history. Keys are left out.</span>
      </div>

      {confirming ? (
        <form className="form" onSubmit={remove}>
          <div className="error-text">
            This deletes your account, every activity and raw file, your settings and coach history. It can&apos;t be undone.
            {account.is_owner ? " You own this Talus; other accounts keep working." : ""}
          </div>
          <label className="field">
            <span className="field-label">{account.has_password ? "Password to confirm" : "Type your email to confirm"}</span>
            <input type={account.has_password ? "password" : "email"} autoComplete={account.has_password ? "current-password" : "off"} required value={delPw} onChange={(e) => setDelPw(e.target.value)} />
          </label>
          <div className="actions">
            <button className="btn danger" type="submit" disabled={!!busy || !delPw} aria-busy={busy === "delete"}>Delete my account</button>
            <button type="button" className="btn light outline" onClick={() => setConfirming(false)}>Keep it</button>
          </div>
          {delMsg ? <div className="error-text" role="alert">{delMsg.text}</div> : null}
        </form>
      ) : (
        <div className="actions">
          <button className="link-btn" style={{ fontSize: 13, color: "var(--bad-ink)" }} onClick={() => setConfirming(true)}>Delete account…</button>
        </div>
      )}
    </section>
  );
}
