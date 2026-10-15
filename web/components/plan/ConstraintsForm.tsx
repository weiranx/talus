"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { DAY_NAMES, DAY_SHORT, HILL_TEXT, MAX_WEEKDAY_MIN, MAX_WEEKEND_MIN, WORKOUT_TEXT } from "@/lib/plan";
import type { Constraints, HillSession, PlanPage, Workout } from "@/lib/types";

type Speed = Exclude<Workout, "none">;
const SPEEDS = (Object.keys(WORKOUT_TEXT) as Workout[]).filter((k): k is Speed => k !== "none");
const HILLS = Object.keys(HILL_TEXT) as HillSession[];

function toggled<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

// Training days, time limits and workouts: the plan never puts more on a day than its limit, and
// takes turns with the workouts picked, one speed session a week and one hill session on the hill day.
// `next`: where to go after saving (the setup's next step); `submit`: the button's words.
export default function ConstraintsForm({ current, picks, hasObjective, next, submit }: {
  current: Constraints; picks?: PlanPage["workout_picks"]; hasObjective: boolean; next?: string; submit?: string;
}) {
  const router = useRouter();
  const [days, setDays] = useState<number[]>(current.days);
  const [weekday, setWeekday] = useState(String(current.weekday_min));
  const [weekend, setWeekend] = useState(String(current.weekend_min));
  const [longDay, setLongDay] = useState(current.long_day);
  const [notes, setNotes] = useState(current.notes);
  const [speeds, setSpeeds] = useState<Speed[]>(picks?.workouts ?? current.workouts ?? []);
  const [hills, setHills] = useState<HillSession[]>(picks?.hill_sessions ?? current.hill_sessions ?? []);
  const [strides, setStrides] = useState(current.strides ?? true);
  const [strength, setStrength] = useState(current.strength ?? true);
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
        workouts: speeds, hill_sessions: hills, strides, strength, workout: speeds[0] ?? "none", back_to_back: pair }),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      const body = await res?.json().catch(() => ({}));
      return setMessage({ ok: false, text: typeof body?.detail === "string" ? body.detail
        : `Check the days and minutes: weekdays 15 to ${MAX_WEEKDAY_MIN}, weekend days 15 to ${MAX_WEEKEND_MIN}.` });
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
          <input inputMode="numeric" value={weekday} onChange={(e) => setWeekday(e.target.value)} />
          <span className="muted">Up to {MAX_WEEKDAY_MIN} (12 h)</span></label>
        <label className="field"><span className="field-label">Weekend limit, min</span>
          <input inputMode="numeric" value={weekend} onChange={(e) => setWeekend(e.target.value)} />
          <span className="muted">Up to {MAX_WEEKEND_MIN} (24 h), for the longest ultra days</span></label>
        <label className="field"><span className="field-label">Long day</span>
          <select value={longDay} onChange={(e) => setLongDay(Number(e.target.value))}>
            {DAY_NAMES.map((d, i) => <option key={d} value={i} disabled={!days.includes(i)}>{d}</option>)}
          </select></label>
        <label className="field"><span className="field-label">Notes, such as travel dates</span>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} /></label>
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 10 }}>
        <legend className="field-label" style={{ marginBottom: 6 }}>Workouts</legend>
        <div className="muted" style={{ marginTop: -4 }}>
          {picks && !picks.picked ? "Picked for your kind of plan until you change them. " : ""}
          Speed sessions take turns, one a week in build and taper weeks; hill sessions take turns on the hill day. None picked: no speed work, and the hill day is a steady climb.
        </div>
        <div role="group" aria-label="Speed sessions">
          <div className="muted" style={{ marginBottom: 4 }}>Speed</div>
          <div className="chips">
            {SPEEDS.map((k) => <button key={k} type="button" className="chip" aria-pressed={speeds.includes(k)} onClick={() => setSpeeds(toggled(speeds, k))}>{WORKOUT_TEXT[k]}</button>)}
          </div>
        </div>
        <div role="group" aria-label="Hill day sessions">
          <div className="muted" style={{ marginBottom: 4 }}>Hill day</div>
          <div className="chips">
            {HILLS.map((k) => <button key={k} type="button" className="chip" aria-pressed={hills.includes(k)} onClick={() => setHills(toggled(hills, k))}>{HILL_TEXT[k]}</button>)}
          </div>
        </div>
        <div role="group" aria-label="Extras">
          <div className="muted" style={{ marginBottom: 4 }}>Extras on easy days</div>
          <div className="chips">
            <button type="button" className="chip" aria-pressed={strides} onClick={() => setStrides(!strides)}>Strides</button>
            <button type="button" className="chip" aria-pressed={strength} onClick={() => setStrength(!strength)}>Strength</button>
          </div>
        </div>
      </fieldset>
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
