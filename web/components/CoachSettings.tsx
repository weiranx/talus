"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Link from "next/link";
import Pill from "@/components/Pill";
import type { CostEstimate, Settings } from "@/lib/types";

function money(v: number | null): string {
  if (v == null) return "–";
  if (v < 0.01) return v === 0 ? "$0" : "under 1¢";
  return v < 1 ? `${Math.round(v * 100)}¢` : `$${v.toFixed(2)}`;
}

function costLine(e: CostEstimate | undefined): string {
  if (!e) return "";
  const call = money(e.per_call);
  return `${call.startsWith("under") ? "Under 1¢" : `About ${call}`} a call, ${money(e.per_month)} a month at ${e.calls_per_month} calls${e.from_log ? "" : ", from a typical reply size until the coach has run"}`;
}

const MODES = [
  { key: "brief_model", mode: "brief", label: "Daily brief", when: "after each sync and after your check-in" },
  { key: "analysis_model", mode: "analysis", label: "Workout analysis", when: "once per activity, right after it syncs" },
] as const;

export default function CoachSettings({ coach }: { coach: Settings["coach"] }) {
  const router = useRouter();
  const [enabled, setEnabled] = useState(coach.enabled);
  const [models, setModels] = useState({ brief_model: coach.brief_model, analysis_model: coach.analysis_model });
  const [style, setStyle] = useState(coach.style);
  const [cap, setCap] = useState(coach.cap.limit == null ? "" : String(coach.cap.limit));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const capValue = cap.trim() === "" ? null : Number(cap);
  const capDirty = capValue !== coach.cap.limit;
  const capValid = capValue == null || (Number.isFinite(capValue) && capValue >= 0 && capValue <= 1000);
  const dirty = capDirty || enabled !== coach.enabled || style !== coach.style ||
    models.brief_model !== coach.brief_model || models.analysis_model !== coach.analysis_model;
  const capped = coach.cap.limit != null && coach.cap.spent >= coach.cap.limit;
  const haiku = coach.models.find((m) => m.id.startsWith("claude-haiku"));

  async function save() {
    if (!capValid) return setMessage({ ok: false, text: "The cap is a dollar amount from 0 to 1000, or blank." });
    setSaving(true);
    setMessage(null);
    const res = await fetch("/api/settings/coach", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled, ...models, style, ...(capDirty ? { monthly_cap_usd: capValue } : {}) }),
    }).catch(() => null);
    setSaving(false);
    if (!res) return setMessage({ ok: false, text: "Could not save. Is the engine running?" });
    if (!res.ok) return setMessage({ ok: false, text: `Could not save (${res.status}).` });
    setMessage({ ok: true, text: "Saved. The next brief and analysis use these settings." });
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">AI coach</h2>
        {!coach.key_set ? <Pill tone="warn">No API key</Pill> : coach.enabled ? <Pill tone="good">On</Pill> : <Pill tone="neutral">Off</Pill>}
      </div>

      <label className="toggle">
        <input type="checkbox" role="switch" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>
          <span style={{ fontWeight: 600 }}>Use the AI coach</span>
          <span className="muted" style={{ display: "block" }}>
            Off shows the rule-based call and summary instead, with no API calls. Sessions always come from the engine either way.
          </span>
        </span>
      </label>
      <div className="muted" style={{ fontSize: 14 }}>
        Tell the coach what kind of training you want under <Link href="/coach">Coach, Your training goals</Link>.
      </div>
      {!coach.key_set ? (
        <div className="muted">
          {coach.cap.ceiling == null
            ? <>Add <code>ANTHROPIC_API_KEY</code> to the engine&apos;s .env and restart it.</>
            : "The owner of this Talus hasn't added an Anthropic key yet."} Until then Talus shows rule-based text.
        </div>
      ) : null}

      <div className="form-grid">
        <label className="field">
          <span className="field-label">Coaching style</span>
          <select value={style} disabled={!enabled} onChange={(e) => setStyle(e.target.value)}>
            {coach.styles.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
          <span className="muted">
            {coach.styles.find((o) => o.key === style)?.description} Style changes the voice only: the call, the session and the number check stay the same.
          </span>
        </label>
        {MODES.map((m) => {
          const est = coach.estimate[m.mode][models[m.key]];
          return (
            <label key={m.key} className="field">
              <span className="field-label">{m.label} model</span>
              <select value={models[m.key]} disabled={!enabled} onChange={(e) => setModels({ ...models, [m.key]: e.target.value })}>
                {coach.models.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
              <span className="muted">Runs {m.when}. {costLine(est)}.</span>
            </label>
          );
        })}
      </div>
      {haiku && models.brief_model !== haiku.id ? (
        <div className="muted">
          The brief is a short reply, so it could run on {haiku.label} for about {money(coach.estimate.brief[haiku.id]?.per_month ?? null)} a month.
          Every reply still goes through the same number check.
        </div>
      ) : null}

      <label className="field">
        <span className="field-label">Monthly spending cap</span>
        <input inputMode="decimal" value={cap} placeholder={coach.cap.ceiling == null ? "No cap" : ""}
          disabled={!enabled} onChange={(e) => setCap(e.target.value.replace(/[^0-9.]/g, ""))} />
        <span className={capped ? "error-text" : "muted"}>
          {money(coach.cap.spent)} spent this month{coach.cap.limit != null ? ` of $${coach.cap.limit.toFixed(2)}` : ""}.
          {capped ? " Cap reached: rule-based text until next month." : ""}
          {coach.cap.ceiling != null ? ` This Talus allows up to $${coach.cap.ceiling.toFixed(2)} a month per account.` : " In dollars; blank means no cap."}
        </span>
      </label>

      <div className="actions">
        <button className="btn" onClick={save} disabled={!dirty || saving} aria-busy={saving}>{saving ? "Saving" : "Save coach settings"}</button>
      </div>
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
    </section>
  );
}
