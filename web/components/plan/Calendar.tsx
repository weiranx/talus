"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { dayLabel, mi, n0 } from "@/lib/format";
import { DAY_SHORT } from "@/lib/plan";
import type { RunKind, Schedule, ScheduledRun } from "@/lib/types";

const WEEKS = 5;
const STATUS_TEXT = { done: "Done", missed: "Missed", today: "Today", planned: "" } as const;
const KIND_COLOR: Record<RunKind, string> = {
  easy: "var(--cardio)", long: "var(--climb)", hills: "var(--descent)", tempo: "#a3231b", intervals: "#a3231b",
  hike: "var(--climb)", race: "var(--ink)", other: "var(--muted)",
};

function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

type Form = { id: number | null; fromPlan: boolean; day: string; kind: RunKind; miles: string; gain_ft: string; loss_ft: string;
  minutes: string; zone: string; notes: string };

function formFor(r: ScheduledRun | null, day: string): Form {
  if (!r) return { id: null, fromPlan: false, day, kind: "easy", miles: "", gain_ft: "", loss_ft: "", minutes: "", zone: "", notes: "" };
  return { id: r.source === "manual" ? r.id : null, fromPlan: r.source === "plan", day: r.day, kind: r.kind, miles: r.miles.toFixed(1),
    gain_ft: String(Math.round(r.gain_ft)), loss_ft: String(Math.round(r.loss_ft)),
    minutes: r.estimated_minutes ? "" : String(Math.round(r.minutes)), zone: r.zone ? String(r.zone) : "", notes: r.notes };
}

// The calendar of scheduled runs: your own, and the plan's next 7 days. Add a run on any day,
// change its date to move it, or delete it. Editing a plan session saves it as your own run.
export default function Calendar({ initial }: { initial: Schedule }) {
  const router = useRouter();
  const [data, setData] = useState(initial);
  const [start, setStart] = useState(initial.start);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (s: string) => {
    const res = await fetch(`/api/schedule?start=${s}&days=${WEEKS * 7}`).catch(() => null);
    if (res?.ok) setData(await res.json());
  }, []);
  useEffect(() => { if (start !== initial.start) load(start); }, [start, initial.start, load]);

  const byDay = new Map<string, ScheduledRun[]>();
  for (const it of data.items) byDay.set(it.day, [...(byDay.get(it.day) ?? []), it]);
  const days = Array.from({ length: WEEKS * 7 }, (_, i) => addDays(start, i));
  const warned = new Map(data.warnings.map((w) => [w.week, w]));

  async function save() {
    if (!form) return;
    setBusy(true);
    setError(null);
    const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(/,/g, "")));
    const body = { day: form.day, kind: form.kind, miles: num(form.miles) ?? 0, gain_ft: num(form.gain_ft) ?? 0,
      loss_ft: num(form.loss_ft), minutes: num(form.minutes), zone: num(form.zone), notes: form.notes };
    const res = await fetch(form.id ? `/api/schedule/${form.id}` : "/api/schedule", {
      method: form.id ? "PUT" : "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      const b = await res?.json().catch(() => ({}));
      return setError(typeof b?.detail === "string" ? b.detail : "Check the date and numbers.");
    }
    setForm(null);
    await load(start);
    router.refresh();
  }

  async function remove() {
    if (!form?.id) return;
    setBusy(true);
    const res = await fetch(`/api/schedule/${form.id}`, { method: "DELETE" }).catch(() => null);
    setBusy(false);
    if (!res?.ok) return setError("Could not delete.");
    setForm(null);
    await load(start);
    router.refresh();
  }

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    form && setForm({ ...form, [k]: e.target.value });

  return (
    <section className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label">Scheduled runs</h2>
        <div className="actions" style={{ gap: 6 }}>
          <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={() => setStart(addDays(start, -7 * WEEKS))}>Earlier</button>
          <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={() => setStart(initial.start)}>This week</button>
          <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={() => setStart(addDays(start, 7 * WEEKS))}>Later</button>
        </div>
      </div>
      <div className="table-wrap">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(92px, 1fr))", gap: 4, minWidth: 660 }}>
          {DAY_SHORT.map((d) => <div key={d} className="muted" style={{ fontSize: 12, padding: "0 4px" }}>{d}</div>)}
          {days.map((d, i) => {
            const items = byDay.get(d) ?? [];
            const w = warned.get(days[i - (i % 7)]);
            const past = d < data.date;
            return (
              <div key={d} style={{
                minHeight: 92, border: `1px solid ${d === data.date ? "var(--ink)" : "var(--line)"}`, borderRadius: 10,
                padding: 6, display: "flex", flexDirection: "column", gap: 4, background: past ? "var(--bg)" : "#fff",
                outline: w ? "2px solid var(--warn-ink)" : undefined,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ fontWeight: d === data.date ? 700 : 500 }}>{dayLabel(d, { month: "short", day: "numeric" })}</span>
                  <button type="button" className="link-btn" aria-label={`Add a run on ${dayLabel(d, { weekday: "long", month: "long", day: "numeric" })}`}
                    onClick={() => setForm(formFor(null, d))} style={{ fontSize: 14, minHeight: 20 }}>+</button>
                </div>
                {items.map((it, i) => (
                  <button key={`${it.source}-${it.id ?? i}`} type="button" onClick={() => setForm(formFor(it, d))}
                    style={{ textAlign: "left", border: 0, borderLeft: `3px solid ${KIND_COLOR[it.kind]}`, background: it.source === "plan" ? "var(--bg)" : "#f6f7f6",
                      borderRadius: 6, padding: "4px 6px", cursor: "pointer", font: "inherit", fontSize: 12, color: "var(--ink)" }}>
                    <div style={{ fontWeight: 600 }}>{it.title}{it.source === "plan" ? " · plan" : ""}</div>
                    <div className="mono" style={{ fontSize: 11 }}>{mi(it.miles)} mi · {n0(it.gain_ft)} ft</div>
                    {STATUS_TEXT[it.status] ? (
                      <div style={{ fontSize: 11, color: it.status === "done" ? "var(--good-ink)" : it.status === "missed" ? "var(--bad-ink)" : "var(--muted)" }}>
                        {STATUS_TEXT[it.status]}{it.activity ? `: ${mi(it.activity.distance_mi)} mi, ${n0(it.activity.gain_ft)} ft` : ""}
                      </div>
                    ) : null}
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      </div>
      {data.warnings.map((w) => (
        <div key={w.week} style={{ color: "var(--warn-ink)", fontSize: 14 }}>
          Week of {dayLabel(w.week, { month: "short", day: "numeric" })}: {w.text}
        </div>
      ))}
      {form ? (
        <div className="form" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
          <div style={{ fontWeight: 600 }}>
            {form.id || form.fromPlan ? "Edit run" : "Add a run"}{form.fromPlan ? " (saves the plan's session as your own run)" : ""}
          </div>
          <div className="form-grid">
            <label className="field"><span className="field-label">Date</span><input type="date" value={form.day} onChange={set("day")} /></label>
            <label className="field"><span className="field-label">Type</span>
              <select value={form.kind} onChange={set("kind")}>
                {data.kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
              </select></label>
            <label className="field"><span className="field-label">Distance, mi</span><input inputMode="decimal" value={form.miles} onChange={set("miles")} /></label>
            <label className="field"><span className="field-label">Gain, ft</span><input inputMode="numeric" value={form.gain_ft} onChange={set("gain_ft")} /></label>
            <label className="field"><span className="field-label">Descent, ft</span><input inputMode="numeric" value={form.loss_ft} onChange={set("loss_ft")} placeholder="Same as gain" /></label>
            <label className="field"><span className="field-label">Time, min</span><input inputMode="numeric" value={form.minutes} onChange={set("minutes")} placeholder="From your pace" /></label>
            <label className="field"><span className="field-label">Zone</span>
              <select value={form.zone} onChange={set("zone")}>
                <option value="">Any</option>
                {(data.zone_model?.list ?? []).map((z) => <option key={z.zone} value={z.zone}>Zone {z.zone}, {z.name.toLowerCase()} ({z.text})</option>)}
              </select></label>
            <label className="field"><span className="field-label">Notes</span><input value={form.notes} onChange={set("notes")} maxLength={300} /></label>
          </div>
          <div className="actions">
            <button className="btn" disabled={busy || !form.day} onClick={save}>{busy ? "Saving" : "Save run"}</button>
            {form.id ? <button className="btn danger" disabled={busy} onClick={remove}>Delete</button> : null}
            <button className="btn light outline" onClick={() => { setForm(null); setError(null); }}>Cancel</button>
            {error ? <span className="error-text">{error}</span> : null}
          </div>
        </div>
      ) : null}
      <div className="muted">
        Tap a day&apos;s + to add a run, or a run to change it; a new date moves it. Your own run replaces the plan&apos;s session that day.
        Runs match the activity you do that day.
      </div>
    </section>
  );
}
