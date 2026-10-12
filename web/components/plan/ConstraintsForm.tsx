"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { DAY_NAMES, DAY_SHORT, WORKOUT_TEXT } from "@/lib/plan";
import type { Constraints, Workout } from "@/lib/types";

// Training days and time limits: the plan never puts more on a day than its limit.
// `next`: where to go after saving (the setup's next step); `submit`: the button's words.
export default function ConstraintsForm({ current, hasObjective, next, submit }: {
  current: Constraints; hasObjective: boolean; next?: string; submit?: string;
}) {
  const router = useRouter();
  const [days, setDays] = useState<number[]>(current.days);
  const [weekday, setWeekday] = useState(String(current.weekday_min));
  const [weekend, setWeekend] = useState(String(current.weekend_min));
  const [longDay, setLongDay] = useState(current.long_day);
  const [notes, setNotes] = useState(current.notes);
  const [workout, setWorkout] = useState<Workout>(current.workout ?? "none");
  const [pair, setPair] = useState(current.back_to_back ?? true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const toggle = (d: number) => setDays(days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort());

  async function save() {
    setBusy(true);
    setMessage(null);
    const res = await fetch("/api/plan/constraints", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ days, weekday_min: Number(weekday), weekend_min: Number(weekend), long_day: longDay, notes,
        workout, back_to_back: pair }),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      const body = await res?.json().catch(() => ({}));
      return setMessage({ ok: false, text: typeof body?.detail === "string" ? body.detail : "Check the days and minutes." });
    }
    if (next) return router.push(next);
    setMessage({ ok: true, text: hasObjective ? "Saved. A new plan is drafted from them for you to review." : "Saved." });
    router.refresh();
  }

  return (
    <div className="form">
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="field-label" style={{ marginBottom: 6 }}>Days you can train</legend>
        <div className="scale">
          {DAY_SHORT.map((d, i) => (
            <button key={d} type="button" aria-pressed={days.includes(i)} onClick={() => toggle(i)} style={{ minWidth: 56 }}>{d}</button>
          ))}
        </div>
      </fieldset>
      <div className="form-grid">
        <label className="field"><span className="field-label">Weekday limit, min</span>
          <input inputMode="numeric" value={weekday} onChange={(e) => setWeekday(e.target.value)} /></label>
        <label className="field"><span className="field-label">Weekend limit, min</span>
          <input inputMode="numeric" value={weekend} onChange={(e) => setWeekend(e.target.value)} /></label>
        <label className="field"><span className="field-label">Long day</span>
          <select value={longDay} onChange={(e) => setLongDay(Number(e.target.value))}>
            {DAY_NAMES.map((d, i) => <option key={d} value={i} disabled={!days.includes(i)}>{d}</option>)}
          </select></label>
        <label className="field"><span className="field-label">Speed work, once a week</span>
          <select value={workout} onChange={(e) => setWorkout(e.target.value as Workout)}>
            {(Object.keys(WORKOUT_TEXT) as Workout[]).map((k) => <option key={k} value={k}>{WORKOUT_TEXT[k]}</option>)}
          </select></label>
        <label className="field"><span className="field-label">Notes, such as travel dates</span>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} /></label>
      </div>
      <label style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 14 }}>
        <input type="checkbox" checked={pair} onChange={(e) => setPair(e.target.checked)} style={{ marginTop: 3 }} />
        <span>Back-to-back long days for long objectives <span className="muted">(about 7 h or more: two long days in a row instead of one huge one)</span></span>
      </label>
      <div className="actions">
        <button className="btn" disabled={busy || !days.includes(longDay)} onClick={save} aria-busy={busy}>{busy ? (next ? "Drafting your plan" : "Saving") : submit ?? "Save training days"}</button>
        {message ? <span className={message.ok ? "ok-text" : "error-text"}>{message.text}</span> : null}
      </div>
    </div>
  );
}
