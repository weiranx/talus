"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AdminOverview, CostEstimate } from "@/lib/types";

function money(v: number | null): string {
  if (v == null) return "–";
  if (v < 0.01) return v === 0 ? "$0" : "under 1¢";
  return v < 1 ? `${Math.round(v * 100)}¢` : `$${v.toFixed(2)}`;
}

function costLine(e: CostEstimate | undefined, unit = "a call"): string {
  if (!e) return "";
  const call = money(e.per_call);
  return `${call.startsWith("under") ? "Under 1¢" : `About ${call}`} ${unit}, ${money(e.per_month)} a month on your account`;
}

const MODES = [
  { key: "brief_model", mode: "brief", label: "Daily brief and quick replies", when: "Today's call, the Load take, activity names and reading typed answers." },
  { key: "analysis_model", mode: "analysis", label: "Longer reads", when: "Workout analysis, the plan's read, weekly review and coach chat." },
  { key: "plan_model", mode: "plan", label: "Plan drafting", when: "Building each training plan draft and checking it a second time, from everything known about the athlete." },
] as const;

// The coach's models, set once for every account on the admin page.
export default function CoachModelsCard({ data }: { data: AdminOverview["coach_models"] }) {
  const router = useRouter();
  const [models, setModels] = useState({ brief_model: data.brief_model, analysis_model: data.analysis_model, plan_model: data.plan_model });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const dirty = (Object.keys(models) as (keyof typeof models)[]).some((k) => models[k] !== data[k]);

  async function save() {
    setBusy(true);
    setMsg(null);
    const res = await fetch("/api/admin/coach-models", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(models),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) return setMsg({ ok: false, text: res ? `Could not save (${res.status}).` : "Could not save. Is the engine running?" });
    setMsg({ ok: true, text: "Saved. Every account's next coach reply uses these models." });
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">Coach models</h2>
      <div className="muted">One choice for every account. Each account still picks its coaching style, its cap and whether the coach is on.</div>
      <div className="form-grid">
        {MODES.map((m) => (
          <label key={m.key} className="field">
            <span className="field-label">{m.label}</span>
            <select value={models[m.key]} disabled={busy} onChange={(e) => setModels({ ...models, [m.key]: e.target.value })}>
              {data.models.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
            <span className="muted">{m.when} {costLine(data.estimate[m.mode]?.[models[m.key]], m.mode === "plan" ? "a draft" : "a call")}.</span>
          </label>
        ))}
      </div>
      {!data.chosen ? <div className="muted">Not picked here yet, so these are {data.env ? <>the <code>TALUS_COACH_MODEL</code> setting</> : "the defaults"}.</div> : null}
      <div className="actions">
        <button className="btn" onClick={save} disabled={!dirty || busy} aria-busy={busy}>{busy ? "Saving" : "Save models"}</button>
      </div>
      {msg ? <div className={msg.ok ? "ok-text" : "error-text"} role="status">{msg.text}</div> : null}
    </section>
  );
}
