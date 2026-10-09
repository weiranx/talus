"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { DAY_NAMES, DAY_SHORT } from "@/lib/plan";
import type { Constraints } from "@/lib/types";

// Training days and time limits: the plan never puts more on a day than its limit.
export default function ConstraintsForm({ current, hasObjective }: { current: Constraints; hasObjective: boolean }) {
  const router = useRouter();
  const [days, setDays] = useState<number[]>(current.days);
  const [weekday, setWeekday] = useState(String(current.weekday_min));
  const [weekend, setWeekend] = useState(String(current.weekend_min));
  const [longDay, setLongDay] = useState(current.long_day);
  const [notes, setNotes] = useState(current.notes);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const toggle = (d: number) => setDays(days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort());

  async function save() {
    setBusy(true);
    setMessage(null);
    const res = await fetch("/api/plan/constraints", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ days, weekday_min: Number(weekday), weekend_min: Number(weekend), long_day: longDay, notes }),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      const body = await res?.json().catch(() => ({}));
      return setMessage({ ok: false, text: typeof body?.detail === "string" ? body.detail : "Check the days and minutes." });
    }
    setMessage({ ok: true, text: hasObjective ? "Saved. A new plan is drafted from them." : "Saved." });
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
        <label className="field"><span className="field-label">Notes, such as travel dates</span>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} /></label>
      </div>
      <div className="actions">
        <button className="btn" disabled={busy || !days.includes(longDay)} onClick={save}>{busy ? "Saving" : "Save training days"}</button>
        {message ? <span className={message.ok ? "ok-text" : "error-text"}>{message.text}</span> : null}
      </div>
    </div>
  );
}
