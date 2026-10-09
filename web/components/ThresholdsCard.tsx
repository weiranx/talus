"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Pill from "@/components/Pill";
import Link from "next/link";
import { dayLabel, n0, pct } from "@/lib/format";
import type { Confidence, InferredThresholds, SettingsThresholds } from "@/lib/types";

const CONF_TONE: Record<Confidence, "good" | "warn" | "bad" | "neutral"> = {
  high: "good", medium: "neutral", low: "warn", provisional: "warn", none: "bad",
};

type Form = { resting: string; aet: string; ant: string; max: string; sex: "male" | "female"; pace: string };

function paceText(s: number | null): string {
  if (s == null) return "";
  const r = Math.round(s);
  return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, "0")}`;
}

function paceSeconds(text: string): number | null | undefined {
  if (!text.trim()) return null;
  const m = text.trim().match(/^(\d{1,2}):([0-5]\d)$/);
  return m ? Number(m[1]) * 60 + Number(m[2]) : undefined;
}

function toForm(th: SettingsThresholds | null): Form {
  return {
    resting: th ? String(Math.round(th.resting)) : "",
    // A provisional AeT is the 88% rule, so leave it blank and let the engine keep applying it.
    aet: th && !th.aet_provisional ? String(Math.round(th.aet)) : "",
    ant: th ? String(Math.round(th.ant)) : "",
    max: th ? String(Math.round(th.max)) : "",
    sex: th?.sex ?? "male",
    pace: paceText(th?.threshold_pace_s_per_mi ?? null),
  };
}

const FIELDS: { key: "resting" | "aet" | "ant" | "max"; label: string; hint: string }[] = [
  { key: "resting", label: "Resting", hint: "first thing in the morning" },
  { key: "aet", label: "Aerobic threshold (AeT)", hint: "blank uses 88% of AnT, marked provisional" },
  { key: "ant", label: "Anaerobic threshold (AnT)", hint: "your hardest steady 30 to 60 min" },
  { key: "max", label: "Max", hint: "highest you have seen" },
];

export default function ThresholdsCard({ current, inferred }: { current: SettingsThresholds | null; inferred: InferredThresholds | null }) {
  const router = useRouter();
  const [form, setForm] = useState<Form>(toForm(current));
  const [editing, setEditing] = useState(current == null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const ant = Number(form.ant);
  const aetPreview = form.aet ? Number(form.aet) : ant ? Math.round(ant * 0.88) : null;

  async function save() {
    const pace = paceSeconds(form.pace);
    if (pace === undefined) {
      setMessage({ ok: false, text: "Write threshold pace as minutes:seconds per mile, like 8:30." });
      return;
    }
    setSaving(true);
    setMessage(null);
    const res = await fetch("/api/settings/thresholds", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        resting: Number(form.resting), aet: form.aet ? Number(form.aet) : null, ant, max: Number(form.max),
        sex: form.sex, threshold_pace_s_per_mi: pace,
      }),
    }).catch(() => null);
    setSaving(false);
    if (!res) return setMessage({ ok: false, text: "Could not save. Is the engine running?" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof body.detail === "string" ? body.detail : "Check each number is a heart rate in bpm.";
      return setMessage({ ok: false, text: detail });
    }
    setMessage({ ok: true, text: `Saved. Rescored ${body.rescored} activities with the new zones.` });
    setEditing(false);
    router.refresh();
  }

  function useInferred() {
    if (!inferred) return;
    const next = { ...form };
    if (inferred.ant.value != null) next.ant = String(inferred.ant.value);
    // A provisional AeT is just the 88% rule: leave the box blank so the engine applies it and labels it.
    if (inferred.aet.value != null) next.aet = inferred.aet.confidence === "provisional" ? "" : String(inferred.aet.value);
    // Recorded max is a floor: a true max may be higher, so never lower the one already set.
    if (inferred.max.value != null) next.max = String(Math.max(inferred.max.value, Number(form.max) || 0));
    setForm(next);
    setEditing(true);
    setMessage({ ok: true, text: "Filled in from your runs. Check them, then save." });
  }

  const complete = form.resting && form.ant && form.max;

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Heart rate thresholds</h2>
        {current ? (
          current.aet_provisional
            ? <Pill tone="warn">AeT provisional</Pill>
            : <Pill tone="neutral">{current.source === "manual" ? "Set by you" : current.source.startsWith("inferred") ? "From your runs" : current.source}</Pill>
        ) : <Pill tone="bad">Not set</Pill>}
      </div>

      {current ? (
        <>
          <div className="stat-grid">
            {FIELDS.map((f) => (
              <div key={f.key} className="stat">
                <div className="muted">{f.key === "aet" ? "AeT" : f.key === "ant" ? "AnT" : f.label}</div>
                <div className="v">{n0(current[f.key])}</div>
              </div>
            ))}
          </div>
          <div className="muted">
            Aerobic gap <span className="mono" style={{ color: "var(--ink)" }}>{pct(current.aerobic_gap)}</span>, goal under 10%.
            {current.aet_provisional ? " AeT is 88% of AnT until you set it or Talus estimates it from your runs." : ""}
          </div>
        </>
      ) : (
        <div className="muted">Talus needs these to score heart rate, split time into zones and work out cardio load.</div>
      )}

      {inferred ? (
        <div className="inferred">
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
            <div>
              <div style={{ fontWeight: 600 }}>Estimated from your runs</div>
              <div className="muted">{inferred.runs_with_hr} runs with heart rate in the last {inferred.days} days</div>
            </div>
            <button className="btn light outline" onClick={useInferred}
              disabled={inferred.ant.value == null}>Use these</button>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col"></th>
                  <th scope="col" className="num">Yours</th>
                  <th scope="col" className="num">From runs</th>
                  <th scope="col">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {(["aet", "ant", "max"] as const).map((k) => {
                  const e = inferred[k];
                  return (
                    <tr key={k}>
                      <td>{k === "aet" ? "AeT" : k === "ant" ? "AnT" : "Max"}</td>
                      <td className="num">{current ? n0(current[k]) : "–"}</td>
                      <td className="num">{n0(e.value)}</td>
                      <td><Pill tone={CONF_TONE[e.confidence]}>{e.confidence === "none" ? "no data" : e.confidence}</Pill></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <ul className="method-list">
            {(["ant", "aet", "max"] as const).map((k) => {
              const e = inferred[k];
              return (
                <li key={k}>
                  <span style={{ fontWeight: 600 }}>{k === "aet" ? "AeT" : k === "ant" ? "AnT" : "Max"}:</span> {e.method}
                  {e.activities.length ? <>{". From "}{e.activities.map((a, i) => (
                    <span key={a.id}>{i ? ", " : ""}<Link href={`/activities/${a.id}`}>{a.name ?? "Activity"} {dayLabel(a.day, { month: "short", day: "numeric" })}</Link></span>
                  ))}</> : null}.
                </li>
              );
            })}
          </ul>
          <div className="muted">A lab or field test beats these. Whatever you save here is what Talus uses.</div>
        </div>
      ) : null}

      {editing ? (
        <form className="form" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <div className="form-grid">
            {FIELDS.map((f) => (
              <label key={f.key} className="field">
                <span className="field-label">{f.label}, bpm</span>
                <input inputMode="numeric" value={form[f.key]}
                  placeholder={f.key === "aet" && aetPreview ? `${aetPreview} (88% of AnT)` : ""}
                  onChange={(e) => setForm({ ...form, [f.key]: e.target.value.replace(/[^\d.]/g, "") })} />
                <span className="muted">{f.hint}</span>
              </label>
            ))}
            <label className="field">
              <span className="field-label">Threshold pace, per mile</span>
              <input value={form.pace} placeholder="8:30" onChange={(e) => setForm({ ...form, pace: e.target.value })} />
              <span className="muted">optional, scores runs that have no heart rate</span>
            </label>
            <label className="field">
              <span className="field-label">Load curve</span>
              <select value={form.sex} onChange={(e) => setForm({ ...form, sex: e.target.value as Form["sex"] })}>
                <option value="male">Male</option>
                <option value="female">Female</option>
              </select>
              <span className="muted">sets how fast cardio load rises with heart rate</span>
            </label>
          </div>
          <div className="actions">
            <button className="btn" type="submit" disabled={!complete || saving}>{saving ? "Saving and rescoring" : "Save and rescore"}</button>
            {current ? <button className="btn light outline" type="button" onClick={() => { setForm(toForm(current)); setEditing(false); setMessage(null); }}>Cancel</button> : null}
          </div>
          <div className="muted">Saving rescores every activity, so loads and zone minutes use the new numbers.</div>
        </form>
      ) : (
        <div className="actions">
          <button className="btn light outline" onClick={() => { setEditing(true); setMessage(null); }}>Change</button>
        </div>
      )}
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
    </section>
  );
}
