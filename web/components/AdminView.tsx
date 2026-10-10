"use client";

import { useRouter } from "next/navigation";
import { Fragment, useState } from "react";
import type { AdminAccount, AdminOverview } from "@/lib/types";

type Msg = { ok: boolean; text: string } | null;

const ACTIONS: Record<string, string> = {
  disable: "Turned off sign-in",
  enable: "Turned on sign-in",
  "sign-out": "Signed out everywhere",
  coach_ceiling: "Changed coach ceiling",
  signup_open: "Opened sign-ups",
  signup_closed: "Closed sign-ups",
};

function when(iso: string | null, withTime = false): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}) });
}

function ago(iso: string | null): string {
  if (!iso) return "Never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 10) return "Just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return when(iso);
}

function bytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${units[i]}`;
}

const usd = (n: number) => `$${n.toFixed(2)}`;

async function send(path: string, method: string, body?: object): Promise<{ ok: boolean; detail?: string }> {
  const res = await fetch(path, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).catch(() => null);
  if (!res) return { ok: false, detail: "Could not reach the engine." };
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, detail: typeof data.detail === "string" ? data.detail : undefined };
}

function AccountActions({ a, onDone }: { a: AdminAccount; onDone: (m: Msg) => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [ceiling, setCeiling] = useState(a.coach.custom_ceiling && a.coach.ceiling !== null ? String(a.coach.ceiling) : "");

  async function run(key: string, path: string, method: string, ok: string, body?: object) {
    setBusy(key);
    const r = await send(path, method, body);
    setBusy(null);
    onDone(r.ok ? { ok: true, text: ok } : { ok: false, text: r.detail ?? "That didn't work." });
    if (r.ok) router.refresh();
  }

  if (a.is_admin) return <div className="muted">Your account. Change it from Settings.</div>;
  const value = Number(ceiling);
  const validCeiling = ceiling === "" || (value >= 0 && value <= 1000);
  return (
    <div className="actions">
      {a.disabled_at ? (
        <button className="btn light outline" disabled={!!busy} aria-busy={busy === "enable"}
          onClick={() => run("enable", `/api/admin/accounts/${a.id}/enable`, "POST", `${a.email} can sign in again.`)}>
          Turn on sign-in
        </button>
      ) : (
        <button className="btn light outline" disabled={!!busy} aria-busy={busy === "disable"}
          onClick={() => run("disable", `/api/admin/accounts/${a.id}/disable`, "POST", `${a.email} is signed out and can't sign in.`)}>
          Turn off sign-in
        </button>
      )}
      <button className="btn light outline" disabled={!!busy || a.sessions === 0} aria-busy={busy === "sign-out"}
        onClick={() => run("sign-out", `/api/admin/accounts/${a.id}/sign-out`, "POST", `${a.email} is signed out everywhere.`)}>
        Sign out everywhere
      </button>
      {a.is_owner ? null : (
        <form className="inline-field" onSubmit={(e) => {
          e.preventDefault();
          run("ceiling", `/api/admin/accounts/${a.id}/coach-ceiling`, "PUT",
            ceiling === "" ? "Coach ceiling back to the server default." : `Coach ceiling set to ${usd(value)} a month.`,
            { ceiling_usd: ceiling === "" ? null : value });
        }}>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <span className="muted">Coach ceiling $</span>
            <input type="number" inputMode="decimal" min={0} max={1000} step={0.5} placeholder="Default"
              value={ceiling} onChange={(e) => setCeiling(e.target.value)} style={{ width: 110 }} />
          </label>
          <button className="btn light outline" type="submit" disabled={!!busy || !validCeiling} aria-busy={busy === "ceiling"}>Save</button>
        </form>
      )}
    </div>
  );
}

export default function AdminView({ data }: { data: AdminOverview }) {
  const router = useRouter();
  const [open, setOpen] = useState<number | null>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const s = data.system;
  const month = new Date(`${data.month}T12:00:00`).toLocaleDateString("en-US", { month: "long" });

  async function toggleSignup() {
    setBusy(true);
    const r = await send("/api/admin/signup", "PUT", { open: !data.signup.open });
    setBusy(false);
    setMsg(r.ok ? { ok: true, text: data.signup.open ? "Sign-ups closed." : "Sign-ups open." } : { ok: false, text: r.detail ?? "That didn't work." });
    if (r.ok) router.refresh();
  }

  return (
    <>
      <section className="card">
        <h2 className="label">Accounts ({data.accounts.length})</h2>
        <div className="table-wrap">
          <table style={{ minWidth: 860 }}>
            <thead>
              <tr>
                <th>Email</th>
                <th>Sign-in</th>
                <th>Created</th>
                <th>Last active</th>
                <th className="num">Activities</th>
                <th>Data source</th>
                <th>Last sync</th>
                <th className="num">Coach, {month}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.accounts.map((a) => (
                <Fragment key={a.id}>
                  <tr>
                    <td style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>
                      {a.email}
                      {a.is_owner ? <span className="pill neutral" style={{ marginLeft: 8 }}>Owner</span> : null}
                      {a.disabled_at ? <span className="pill bad" style={{ marginLeft: 8 }}>Off</span> : null}
                    </td>
                    <td>{a.sign_in.length ? a.sign_in.map((m) => (m === "google" ? "Google" : "Password")).join(", ") : "None"}</td>
                    <td>{when(a.created_at)}</td>
                    <td title={a.last_seen ?? undefined}>{ago(a.last_seen)}</td>
                    <td className="num">{a.activities.toLocaleString()}</td>
                    <td>{a.connected.length ? a.connected.join(", ") : a.sources.length ? a.sources.join(", ") : <span className="muted">None</span>}</td>
                    <td>
                      {a.last_sync ? (
                        <span title={`${a.last_sync.source}, ${when(a.last_sync.at, true)}`}>
                          {ago(a.last_sync.at)}{a.last_sync.ok ? "" : <span className="error-text"> (failed)</span>}
                        </span>
                      ) : <span className="muted">Never</span>}
                    </td>
                    <td className="num">
                      {usd(a.coach.spent)}
                      <span className="muted"> / {a.coach.limit === null ? "no cap" : usd(a.coach.limit)}</span>
                    </td>
                    <td>
                      <button className="link-btn" aria-expanded={open === a.id} onClick={() => setOpen(open === a.id ? null : a.id)}>
                        {open === a.id ? "Close" : "Manage"}
                      </button>
                    </td>
                  </tr>
                  {open === a.id ? (
                    <tr>
                      <td colSpan={9} style={{ whiteSpace: "normal", borderTop: 0, paddingTop: 0 }}>
                        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                          <div className="muted">
                            {a.sessions} active {a.sessions === 1 ? "session" : "sessions"} · {bytes(a.disk_bytes)} on disk
                            {a.coach.own_cap !== null ? ` · picked their own ${usd(a.coach.own_cap)} coach cap` : ""}
                            {a.coach.ceiling !== null ? ` · ceiling ${usd(a.coach.ceiling)}${a.coach.custom_ceiling ? "" : " (server default)"}` : ""}
                          </div>
                          <AccountActions a={a} onDone={setMsg} />
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        {msg ? <div className={msg.ok ? "ok-text" : "error-text"} role="status">{msg.text}</div> : null}
        <div className="muted">Counts only. Nobody&apos;s activities, check-ins or keys are shown here.</div>
      </section>

      <div className="row">
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <h2 className="label">Sign-ups</h2>
            <div className="actions">
              <span className={`pill ${data.signup.open ? "warn" : "neutral"}`}>{data.signup.open ? "Open" : "Closed"}</span>
              <button className="btn light outline" onClick={toggleSignup} disabled={busy} aria-busy={busy}>
                {data.signup.open ? "Close sign-ups" : "Open sign-ups"}
              </button>
            </div>
            <div className="muted">
              {data.signup.open
                ? "Anyone who can reach this Talus can make an account, with email or Google."
                : "Nobody new can make an account. Existing accounts sign in as usual."}
              {data.signup.chosen === null ? ` Set by TALUS_SIGNUP until you change it here.` : ""}
            </div>
          </section>

          <section className="card">
            <h2 className="label">Recent admin actions</h2>
            {data.log.length ? (
              <ul className="method-list" style={{ listStyle: "none", paddingLeft: 0 }}>
                {data.log.map((e) => (
                  <li key={e.id}>
                    <span style={{ color: "var(--ink)" }}>{ACTIONS[e.action] ?? e.action}</span>
                    {e.target ? ` for ${e.target}` : ""}
                    {e.action === "coach_ceiling" && e.detail ? ` to ${e.detail.to === null ? "the default" : usd(Number(e.detail.to))}` : ""}
                    {" · "}{when(e.at, true)}
                  </li>
                ))}
              </ul>
            ) : <div className="muted">Nothing yet.</div>}
          </section>
        </div>

        <section className="card" style={{ flex: "1 1 380px" }}>
          <h2 className="label">System</h2>
          <dl className="kv">
            <dt>Version</dt>
            <dd className="mono">{s.revision ? s.revision.slice(0, 7) : "unknown"}</dd>
            <dt>Scoring model</dt>
            <dd className="mono">{s.model_version}</dd>
            <dt>Coach</dt>
            <dd>
              Brief <span className="mono">{s.coach.brief_model}</span>, analysis <span className="mono">{s.coach.analysis_model}</span>,
              prompt <span className="mono">{s.coach.prompt_version}</span>{s.coach.key_set ? "" : <span className="error-text"> (no Anthropic key)</span>}
            </dd>
            <dt>Coach ceiling</dt>
            <dd>{usd(s.server_ceiling_usd)} a month for each account but the owner</dd>
            <dt>Data folder</dt>
            <dd>{bytes(s.data_bytes)}</dd>
            <dt>Disk</dt>
            <dd>{bytes(s.disk.free)} free of {bytes(s.disk.total)}</dd>
          </dl>
        </section>
      </div>
    </>
  );
}
