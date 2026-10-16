"use client";

import { useRef, useState } from "react";
import { dayLabel, mi, n0 } from "@/lib/format";
import { planRequest, type CoachingPlan, type WorkoutOutcome } from "@/lib/plan-coaching";
import styles from "./coaching.module.css";

export default function WorkoutOutcomes({ data, onSaved }: { data: CoachingPlan; onSaved: () => Promise<void> }) {
  const [day, setDay] = useState("");
  const selected = data.recent_sessions.find((s) => s.day === day) ?? data.recent_sessions.at(-1);
  return <details><summary>Did your workouts achieve their purpose?</summary>
    {!selected ? <p className="muted">Feedback opens when a planned training day arrives.</p> : <>
      <label>Session<select value={selected.day} onChange={(e) => setDay(e.target.value)} aria-label="Workout for feedback">
        {data.recent_sessions.map((s) => <option key={s.day} value={s.day}>{dayLabel(s.day, { weekday: "short", month: "short", day: "numeric" })} · {s.planned.kind.replaceAll("_", " ")}{s.outcome ? " · reported" : ""}</option>)}
      </select></label>
      <OutcomeForm key={selected.day + JSON.stringify(selected.outcome)} selected={selected} onSaved={onSaved} />
    </>}
    <p className="muted">Intent is your assessment. Missing feedback stays unknown. Sync a completed activity before rating it.</p>
  </details>;
}

function OutcomeForm({ selected, onSaved }: { selected: CoachingPlan["recent_sessions"][number]; onSaved: () => Promise<void> }) {
  const [intent, setIntent] = useState<WorkoutOutcome["intent"]>(selected.outcome?.intent ?? (selected.completed ? "met" : "missed"));
  const [effort, setEffort] = useState(selected.outcome?.effort?.toString() ?? "");
  const [reason, setReason] = useState(selected.outcome?.reason ?? "");
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const s = selected.planned;
  return <form className={styles.form} onSubmit={async (e) => {
    e.preventDefault(); if (guard.current) return; guard.current = true; setBusy(true); setError(""); setNotice("");
    try {
      await planRequest("/api/plan/outcomes", "PUT", { day: selected.day, intent, effort: intent === "missed" || !effort ? null : Number(effort), reason: reason || null });
      setNotice("Workout feedback saved."); await onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save feedback. Your answer is still here."); }
    finally { guard.current = false; setBusy(false); }
  }}>
    <p><strong>Planned:</strong> {mi(s.miles)} mi · {n0(s.gain_ft)} ft up · {Math.round(s.minutes)} min. {s.benefit}</p>
    {s.source === "planned_reference" ? <p className="muted">This older plan has a weekly reference rather than a saved session snapshot.</p> : null}
    <p><strong>Completed:</strong> {selected.completed ? `${mi(selected.actual.miles)} mi · ${n0(selected.actual.gain_ft)} ft up · ${Math.round(selected.actual.minutes)} min` : "No synced activity"}</p>
    <fieldset disabled={busy}>
      <label>Did it do its job?<select value={intent} onChange={(e) => setIntent(e.target.value as WorkoutOutcome["intent"])}>
        <option value="met" disabled={!selected.completed}>Yes, met the purpose</option><option value="partial" disabled={!selected.completed}>Partly</option><option value="missed" disabled={selected.completed}>Missed session</option>
      </select></label>
      {intent !== "missed" ? <label>Perceived effort, 1–10 (optional)<input type="number" min="1" max="10" step="1" value={effort} onChange={(e) => setEffort(e.target.value)} /></label> : null}
      <label>What affected it? (optional)<select value={reason} onChange={(e) => setReason(e.target.value as typeof reason)}>
        <option value="">Nothing to add</option><option value="busy">Schedule / busy</option><option value="too_hard">Too hard</option><option value="sore">Sore legs</option><option value="terrain">Terrain access</option><option value="other">Other</option>
      </select></label>
      <button className="btn light outline" type="submit">{busy ? "Saving…" : "Save workout feedback"}</button>
    </fieldset>
    {error ? <p className="error-text" role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
  </form>;
}
