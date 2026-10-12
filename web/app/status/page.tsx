import Link from "next/link";
import Pill from "@/components/Pill";
import StatusRefresh from "@/components/StatusRefresh";
import { engine, engineFailed } from "@/lib/api";
import type { CheckState, StatusOverview } from "@/lib/types";

export const dynamic = "force-dynamic";

// Is Talus working: the engine and this web app, each data source's last sync, the activity
// read-ahead and the coach, for the signed-in account. The admin also sees the server. Anyone can
// check the engine is up at /api/health without signing in.

const WEB_REVISION = process.env.TALUS_WEB_REVISION ?? "unknown";
const WEB_BUILT_AT = process.env.TALUS_WEB_BUILT_AT ?? null;

const TONE: Record<CheckState, "good" | "warn" | "bad" | "neutral"> = { ok: "good", warn: "warn", bad: "bad", idle: "neutral" };
const WORD: Record<CheckState, string> = { ok: "Working", warn: "Check", bad: "Not working", idle: "Not set up" };

function State({ state, children }: { state: CheckState; children?: React.ReactNode }) {
  return <Pill tone={TONE[state]}>{children ?? WORD[state]}</Pill>;
}

function when(iso: string | null, withTime = true): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}) });
}

function ago(iso: string | null): string {
  if (!iso) return "Never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 2) return "Just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} days ago`;
}

function uptime(s: number): string {
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 2 * 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} days`;
}

function bytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${units[i]}`;
}

const usd = (n: number) => `$${n.toFixed(2)}`;
const short = (rev: string | null) => (rev ? rev.replace(/^([0-9a-f]{7})[0-9a-f]*/, "$1") : "unknown");
const day = (iso: string | null) => (iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "None yet");

const COACH_WHY: Record<string, string> = {
  off: "The coach is off in Settings, so pages show the rule-based read.",
  no_key: "No Anthropic key on the server, so pages show the rule-based read.",
  cap: "This month's spending reached the cap. The coach is back next month, or raise the cap in Settings.",
  api_error: "The last call to the AI failed, so that page showed the rule-based read. Reloading tries again.",
  recent_errors: "Working now, but a call failed in the last 24 hours.",
};

function sameCommit(a: string, b: string | null): boolean | null {
  const x = a.replace(/-dirty$/, "");
  if (!b || x === "unknown" || b === "unknown") return null;
  return x.startsWith(b) || b.startsWith(x);
}

export default async function StatusPage() {
  let s: StatusOverview | null = null;
  let down: string | null = null;
  try {
    s = await engine<StatusOverview>("/api/status");
  } catch (e) {
    down = engineFailed(e);
  }

  const states: CheckState[] = s
    ? [...s.sources.map((r) => r.state), s.coach.state, ...(s.server ? [s.server.disk_state] : []),
       ...(s.server?.failing_syncs.length ? ["warn" as const] : [])]
    : ["bad"];
  const bad = states.filter((x) => x === "bad").length;
  const warn = states.filter((x) => x === "warn").length;
  const summary: CheckState = bad ? "bad" : warn ? "warn" : "ok";
  const inStep = s ? sameCommit(WEB_REVISION, s.engine.revision) : null;
  const live = Boolean(s && (s.importing?.state === "running" || s.read_ahead.running));

  return (
    <>
      <header className="page-head">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h1 className="title">Status</h1>
          <State state={summary}>
            {summary === "ok" ? "All working" : bad ? `${bad} not working` : `${warn} to check`}
          </State>
        </div>
        <StatusRefresh live={live} />
      </header>

      <section className="card">
        <h2 className="label">Talus</h2>
        <dl className="kv">
          <dt>Engine</dt>
          <dd>
            {s ? (
              <>
                <State state="ok">Up</State>{" "}
                <span className="muted">for {uptime(s.engine.uptime_s)}, version </span>
                <span className="mono">{short(s.engine.revision)}</span>
              </>
            ) : (
              <><State state="bad">Down</State> <span className="muted">{down}</span></>
            )}
          </dd>
          <dt>Web app</dt>
          <dd>
            <span className="mono">{short(WEB_REVISION)}</span>
            {WEB_BUILT_AT ? <span className="muted">, built {when(WEB_BUILT_AT)}</span> : null}
            {inStep === false ? (
              <div className="muted" style={{ marginTop: 4 }}>
                <Pill tone="warn">Different versions</Pill> The engine and the web app were built from different commits.
                That&apos;s normal right after a merge until the web app is deployed too.
              </div>
            ) : null}
          </dd>
          {s ? (<><dt>Scoring model</dt><dd className="mono">{s.engine.model_version}</dd></>) : null}
        </dl>
      </section>

      {s ? (
        <div className="row">
          <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
            <section className="card">
              <h2 className="label">Your data sources</h2>
              {s.sources.length ? (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Source</th><th>State</th><th>Last sync</th><th className="num">New</th></tr>
                    </thead>
                    <tbody>
                      {s.sources.map((r) => (
                        <tr key={r.key}>
                          <td>
                            {r.label}
                            {r.how ? <span className="muted">, {r.how}</span> : null}
                          </td>
                          <td>
                            <State state={r.state}>
                              {r.error ? "Failed" : r.connected === false ? "Not connected" : r.state === "warn" ? (r.at ? "Behind" : "Never synced") : r.at ? "OK" : "Not used yet"}
                            </State>
                          </td>
                          <td title={r.at ? when(r.at) : undefined}>{ago(r.at)}</td>
                          <td className="num">{r.saved ?? "–"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="muted">No data source yet. <Link href="/settings">Connect one in Settings</Link>.</div>
              )}
              {s.sources.filter((r) => r.error).map((r) => (
                <div key={r.key} className="error-text">{r.label}: {r.error}</div>
              ))}
              {s.sources.some((r) => r.state === "warn" && !r.error && r.connected !== false) ? (
                <div className="muted">A synced source that hasn&apos;t run for 3 days is worth a look. Sync from Settings.</div>
              ) : null}
              {s.importing?.state === "running" ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <div>Importing history: {s.importing.done} of {s.importing.total ?? "?"} activities</div>
                  <div className="progress" role="progressbar" aria-label="History import"
                    aria-valuemin={0} aria-valuemax={s.importing.total ?? 0} aria-valuenow={s.importing.done}>
                    <span style={{ width: `${s.importing.total ? (100 * s.importing.done) / s.importing.total : 0}%` }} />
                  </div>
                </div>
              ) : null}
              <dl className="kv">
                <dt>Activities</dt>
                <dd>{s.data.activities.toLocaleString()}</dd>
                <dt>Newest activity</dt>
                <dd>{day(s.data.newest_activity)}</dd>
                <dt>Newest overnight data</dt>
                <dd>{day(s.data.newest_night)}</dd>
              </dl>
            </section>

            <section className="card">
              <h2 className="label">Activity pages</h2>
              {s.read_ahead.waiting_for === "thresholds" ? (
                <div className="muted">Waiting for heart rate thresholds. <Link href="/settings">Set them in Settings</Link>.</div>
              ) : s.read_ahead.total === 0 ? (
                <div className="muted">No activities yet.</div>
              ) : (
                <>
                  <div>
                    {s.read_ahead.ready.toLocaleString()} of {s.read_ahead.total.toLocaleString()} ready to open instantly
                    {s.read_ahead.running ? <span className="muted">, reading the rest in the background</span> : null}
                  </div>
                  <div className="progress" role="progressbar" aria-label="Activities read ahead"
                    aria-valuemin={0} aria-valuemax={s.read_ahead.total} aria-valuenow={s.read_ahead.ready}>
                    <span style={{ width: `${(100 * s.read_ahead.ready) / s.read_ahead.total}%` }} />
                  </div>
                  {s.read_ahead.ready < s.read_ahead.total && !s.read_ahead.running ? (
                    <div className="muted">The rest open a little slower the first time. Reading carries on as you use Talus.</div>
                  ) : null}
                </>
              )}
            </section>
          </div>

          <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
            <section className="card">
              <h2 className="label">Coach</h2>
              <div className="actions">
                <State state={s.coach.state}>{s.coach.state === "ok" ? "Writing" : s.coach.state === "idle" ? "Off" : s.coach.why === "cap" ? "At cap" : s.coach.state === "bad" ? "Not writing" : "Check"}</State>
              </div>
              {s.coach.why ? <div className="muted">{COACH_WHY[s.coach.why]}</div> : null}
              <dl className="kv">
                <dt>Last AI call</dt>
                <dd>
                  {s.coach.last_call ? (
                    <span title={when(s.coach.last_call.at)}>
                      {ago(s.coach.last_call.at)}
                      {s.coach.last_call.ok ? "" : <span className="error-text"> (failed)</span>}
                    </span>
                  ) : "None yet"}
                </dd>
                <dt>Failed, last 24 h</dt>
                <dd>{s.coach.errors_24h}</dd>
                <dt>Spent this month</dt>
                <dd>
                  {usd(s.coach.spent)}
                  <span className="muted"> of {s.coach.limit === null ? "no cap" : usd(s.coach.limit)}</span>
                </dd>
              </dl>
              {s.coach.limit ? (
                <div className="progress" role="progressbar" aria-label="Coach spending this month"
                  aria-valuemin={0} aria-valuemax={s.coach.limit} aria-valuenow={s.coach.spent}>
                  <span style={{ width: `${Math.min(100, (100 * s.coach.spent) / s.coach.limit)}%` }} />
                </div>
              ) : null}
            </section>

            {s.server ? (
              <section className="card">
                <div className="page-head" style={{ alignItems: "center" }}>
                  <h2 className="label">Server</h2>
                  <Link href="/admin" style={{ fontSize: 13 }}>Admin</Link>
                </div>
                <dl className="kv">
                  <dt>Accounts</dt>
                  <dd>{s.server.accounts}, {s.server.active_7d} active this week</dd>
                  <dt>Failed syncs</dt>
                  <dd>
                    {s.server.failing_syncs.length ? s.server.failing_syncs.map((f) => (
                      <div key={f.email}><State state="warn">{f.source}</State> {f.email}, {ago(f.at)}</div>
                    )) : "None"}
                  </dd>
                  <dt>Coach, all accounts</dt>
                  <dd>{usd(s.server.coach_spent)} this month{s.server.coach.key_set ? "" : <span className="error-text"> (no Anthropic key)</span>}</dd>
                  <dt>Coach models</dt>
                  <dd>
                    <span className="mono">{s.server.coach.brief_model}</span>, <span className="mono">{s.server.coach.analysis_model}</span>
                  </dd>
                  <dt>Disk</dt>
                  <dd>
                    <State state={s.server.disk_state}>{bytes(s.server.disk.free)} free</State>
                    <span className="muted"> of {bytes(s.server.disk.total)}, Talus data {bytes(s.server.data_bytes)}</span>
                  </dd>
                  <dt>Sign-ups</dt>
                  <dd>{s.server.signup_open ? "Open" : "Closed"}</dd>
                  <dt>Google sign-in</dt>
                  <dd>{s.server.sign_in.google ? "On" : "Off"}</dd>
                  <dt>intervals.icu app</dt>
                  <dd>
                    {s.server.intervals_oauth.enabled
                      ? `On, ${s.server.intervals_oauth.connected} connected`
                      : "Off until intervals.icu approves the app (accounts use API keys meanwhile)"}
                  </dd>
                </dl>
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
