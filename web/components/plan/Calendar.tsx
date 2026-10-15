"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useState } from "react";
import { Spinner } from "@/components/Loading";
import { type DayDetail, usePlanDetail } from "@/components/plan/PlanDetail";
import { dayLabel, mi, n0 } from "@/lib/format";
import { cover, DAY_SHORT, FOCUS_TEXT } from "@/lib/plan";
import type { RunKind, Schedule, ScheduledRun, WeekProgress } from "@/lib/types";

const WEEKS = 5;  // fetched; `compact` shows the first two until asked for more
const STATUS_TEXT = { done: "Done", missed: "Missed", today: "Today", planned: "" } as const;
const KIND_COLOR: Record<RunKind, string> = {
  easy: "var(--cardio)", long: "var(--climb)", hills: "var(--descent)", tempo: "var(--target)", intervals: "var(--target)",
  hike: "var(--climb)", race: "var(--ink)", other: "var(--muted)", rest: "var(--line)",
};

// A calendar item as the detail sheet shows it: a plan session with its steps, or your own run.
function detailOf(it: ScheduledRun): DayDetail {
  const own = it.source === "manual";
  return { day: it.day, title: it.title, kind: own ? it.kind : it.session ?? it.kind, miles: it.miles, gain_ft: it.gain_ft,
    loss_ft: it.loss_ft, minutes: it.minutes, steps: it.steps, how: it.how ?? (own ? it.detail : null), benefit: it.benefit,
    focus: it.focus, week_note: it.week_note, own, run_id: own ? it.id : null, notes: it.notes,
    activities: it.activity ? [it.activity] : undefined };
}

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

const COVER_COLOR = { covered: "var(--good-ink)", close: "var(--warn-ink)", short: "var(--bad-ink)", over: "var(--warn-ink)" } as const;

// A line under each week: the plan's target against done plus scheduled, in mi and ft up.
function WeekLine({ w, past, onWeek }: { w: WeekProgress | undefined; past: boolean; onWeek?: (start: string) => void }) {
  if (!w) return null;
  const have = { miles: (w.done?.miles ?? 0) + w.ahead.miles, gain_ft: (w.done?.gain_ft ?? 0) + w.ahead.gain_ft };
  const all = cover(w);
  const c = w.dated || all?.state === "over" ? all : null;  // over is over, dated or not
  const sum = `${past ? "Done" : w.done ? "Done + scheduled" : "Scheduled"} ${mi(have.miles)} mi · ${n0(have.gain_ft)} ft up`;
  return (
    <div style={{ gridColumn: "1 / -1", display: "flex", gap: 12, flexWrap: "wrap", fontSize: 12, padding: "0 4px 6px" }}>
      {w.target ? (
        <span>{FOCUS_TEXT[w.target.focus]} target <span className="mono">{mi(w.target.miles)} mi · {n0(w.target.gain_ft)} ft up</span></span>
      ) : null}
      {w.target?.adapted ? <span style={{ color: "var(--warn-ink)" }} title={w.target.adapted.text}>Adapted: {Math.round((1 - w.target.adapted.factor) * 100)}% lighter</span> : null}
      <span className="muted">{sum}</span>
      {c ? <span style={{ color: COVER_COLOR[c.state], fontWeight: 600 }}>
        {c.state === "covered" ? "Covered" : c.state === "over"
          ? `Over target: ${Math.round(Math.max(...Object.values(c.share)) * 100)}%`
          : `${Math.round(Math.min(c.share.miles, c.share.gain_ft) * 100)}% of target`}
      </span> : w.target ? <span className="muted">The plan&apos;s days show a week ahead</span> : null}
      {w.target && onWeek ? <button type="button" className="link-btn" onClick={() => onWeek(w.start)}>Days</button> : null}
    </div>
  );
}

// The calendar of scheduled runs: your own, and the plan's next 7 days. Add a run on any day,
// change its date to move it, or delete it. Editing a plan session saves it as your own run.
export default function Calendar({ initial, compact }: { initial: Schedule; compact?: boolean }) {
  const router = useRouter();
  const [data, setData] = useState(initial);
  const [start, setStart] = useState(initial.start);
  const [form, setForm] = useState<Form | null>(null);
  const [all, setAll] = useState(!compact);
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const detail = usePlanDetail();

  const load = useCallback(async (s: string) => {
    setLoading(true);
    const res = await fetch(`/api/schedule?start=${s}&days=${WEEKS * 7}`).catch(() => null);
    if (res?.ok) setData(await res.json());
    setLoading(false);
  }, []);
  useEffect(() => { if (start !== initial.start) load(start); }, [start, initial.start, load]);
  // A day changed in the detail sheet: show the weeks as they now stand.
  const version = detail?.version ?? 0;
  useEffect(() => { if (version) load(start); }, [version]);  // eslint-disable-line react-hooks/exhaustive-deps

  const byDay = new Map<string, ScheduledRun[]>();
  for (const it of data.items) byDay.set(it.day, [...(byDay.get(it.day) ?? []), it]);
  const ran = new Map<string, NonNullable<Schedule["activities"]>>();
  for (const a of data.activities ?? []) ran.set(a.day, [...(ran.get(a.day) ?? []), a]);
  const days = Array.from({ length: (all ? WEEKS : 2) * 7 }, (_, i) => addDays(start, i));
  const warned = new Map(data.warnings.map((w) => [w.week, w]));
  const weeks = new Map((data.weeks ?? []).map((w) => [w.start, w]));

  async function save() {
    if (!form) return;
    setBusy("save");
    setError(null);
    const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(/,/g, "")));
    const body = { day: form.day, kind: form.kind, miles: num(form.miles) ?? 0, gain_ft: num(form.gain_ft) ?? 0,
      loss_ft: num(form.loss_ft), minutes: num(form.minutes), zone: num(form.zone), notes: form.notes };
    const res = await fetch(form.id ? `/api/schedule/${form.id}` : "/api/schedule", {
      method: form.id ? "PUT" : "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }).catch(() => null);
    setBusy(null);
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
    setBusy("delete");
    const res = await fetch(`/api/schedule/${form.id}`, { method: "DELETE" }).catch(() => null);
    setBusy(null);
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
        <h2 className="label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {all ? "Scheduled runs" : "This week and next"}{loading ? <Spinner label="Loading these weeks" /> : null}
        </h2>
        <div className="actions" style={{ gap: 6 }}>
          {all ? (
            <>
              <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={() => setStart(addDays(start, -7 * WEEKS))}>Earlier</button>
              <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={() => setStart(initial.start)}>This week</button>
              <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={() => setStart(addDays(start, 7 * WEEKS))}>Later</button>
            </>
          ) : null}
          {compact ? (
            <button className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }}
              onClick={() => { setAll(!all); if (all) setStart(initial.start); }}>{all ? "Show 2 weeks" : "Show 5 weeks"}</button>
          ) : null}
        </div>
      </div>
      <div className="table-wrap" aria-busy={loading}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(92px, 1fr))", gap: 4, minWidth: 660,
          opacity: loading ? 0.55 : 1, transition: "opacity 0.15s" }}>
          {DAY_SHORT.map((d) => <div key={d} className="muted" style={{ fontSize: 12, padding: "0 4px" }}>{d}</div>)}
          {days.map((d, i) => {
            const items = byDay.get(d) ?? [];
            const weekEnd = i % 7 === 6;
            const w = warned.get(days[i - (i % 7)]);
            const past = d < data.date;
            return (
              <Fragment key={d}>
              <div style={{
                minHeight: 92, border: `1px solid ${d === data.date ? "var(--ink)" : "var(--line)"}`, borderRadius: 10,
                padding: 6, display: "flex", flexDirection: "column", gap: 4, background: past ? "var(--bg)" : "var(--raised)",
                outline: w ? "2px solid var(--warn-ink)" : undefined,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ fontWeight: d === data.date ? 700 : 500 }}>{dayLabel(d, { month: "short", day: "numeric" })}</span>
                  <button type="button" className="link-btn" aria-label={`Add a run on ${dayLabel(d, { weekday: "long", month: "long", day: "numeric" })}`}
                    onClick={() => setForm(formFor(null, d))} style={{ fontSize: 14, minHeight: 20 }}>+</button>
                </div>
                {items.map((it, i) => (
                  <button key={`${it.source}-${it.id ?? i}`} type="button" onClick={() => (detail ? detail.openDay(detailOf(it)) : setForm(formFor(it, d)))}
                    style={{ textAlign: "left", border: 0, borderLeft: `3px solid ${KIND_COLOR[it.kind]}`, background: it.source === "plan" ? "var(--bg)" : "var(--past)",
                      borderRadius: 6, padding: "4px 6px", cursor: "pointer", font: "inherit", fontSize: 12, color: "var(--ink)" }}>
                    <div style={{ fontWeight: 600 }}>{it.title}{it.source === "plan" ? " · plan" : ""}</div>
                    {it.kind === "rest" ? <div style={{ fontSize: 11 }} className="muted">Day off</div>
                      : <div className="mono" style={{ fontSize: 11 }}>{mi(it.miles)} mi · {n0(it.gain_ft)} ft</div>}
                    {STATUS_TEXT[it.status] ? (
                      <div style={{ fontSize: 11, color: it.status === "done" ? "var(--good-ink)" : it.status === "missed" ? "var(--bad-ink)" : "var(--muted)" }}>
                        {STATUS_TEXT[it.status]}{it.activity ? `: ${mi(it.activity.distance_mi)} mi, ${n0(it.activity.gain_ft)} ft` : ""}
                      </div>
                    ) : null}
                  </button>
                ))}
                {(ran.get(d) ?? []).map((a) => (
                  <Link key={a.id} href={`/activities/${a.id}`}
                    style={{ display: "block", borderLeft: "3px solid var(--good-ink)", background: "var(--past)", borderRadius: 6,
                      padding: "4px 6px", fontSize: 12, color: "var(--ink)", textDecoration: "none" }}>
                    <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</div>
                    <div className="mono" style={{ fontSize: 11 }}>{mi(a.distance_mi)} mi · {n0(a.gain_ft)} ft</div>
                    <div style={{ fontSize: 11, color: "var(--good-ink)" }}>Done</div>
                  </Link>
                ))}
              </div>
              {weekEnd ? <WeekLine w={weeks.get(days[i - 6])} past={d < data.date} onWeek={detail?.openWeek} /> : null}
              </Fragment>
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
            <button className="btn" disabled={!!busy || !form.day} onClick={save} aria-busy={busy === "save"}>{busy === "save" ? "Saving" : "Save run"}</button>
            {form.id ? <button className="btn danger" disabled={!!busy} onClick={remove} aria-busy={busy === "delete"}>Delete</button> : null}
            <button className="btn light outline" onClick={() => { setForm(null); setError(null); }}>Cancel</button>
            {error ? <span className="error-text">{error}</span> : null}
          </div>
        </div>
      ) : null}
      <div className="muted">
        {detail ? <>Tap a run to see how to run it and why, or to change it, move it or take the day off. Tap a day&apos;s + to add a run. </>
          : <>Tap a day&apos;s + to add a run, or a run to change it; a new date moves it. </>}
        Your own run or day off replaces the plan&apos;s session that day; the plan moves the rest of the week around it and eases the next week when the change is big.
        Runs match the activity you do that day.
      </div>
    </section>
  );
}
