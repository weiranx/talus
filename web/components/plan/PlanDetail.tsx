"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Spinner } from "@/components/Loading";
import { dayLabel, mi, n0 } from "@/lib/format";
import { FOCUS_TEXT, HILL_TEXT, hours, KIND_COLOR, minutesText, PLAN_RUN_KIND, RUN_KIND_TEXT, WORKOUT_TEXT } from "@/lib/plan";
import type { PlanWeekDays, RunKind, SessionKind } from "@/lib/types";

// One day on the plan or the calendar, as the detail sheet shows it.
export type DayDetail = {
  day: string;
  title: string;
  kind: SessionKind | RunKind;
  miles: number;
  gain_ft: number;
  loss_ft: number;
  minutes: number;
  steps?: string[];
  how?: string | null;
  benefit?: string | null;
  focus?: string | null;
  week_note?: string | null;
  // The athlete's own run or rest day, with its id on the calendar.
  own?: boolean;
  run_id?: number | null;
  notes?: string;
  activities?: { id: number; name: string | null; distance_mi: number; gain_ft: number; loss_ft: number }[];
};

type Ctx = { openWeek: (start: string, draft?: boolean) => void; openDay: (d: DayDetail) => void; version: number };
const PlanDetailContext = createContext<Ctx | null>(null);

/** Open a week or a day of the plan in the detail sheet; null outside the plan page. */
export function usePlanDetail(): Ctx | null {
  return useContext(PlanDetailContext);
}

// draft: a week of the draft plan, to look at before accepting it; its days can't be changed.
type View = { week?: string; day?: DayDetail; note?: string; draft?: boolean };

function mondayOf(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));
  return t.toISOString().slice(0, 10);
}

function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

// The plan page's detail sheet: tap a week to see its days, tap a day to see how to run it and
// why, and change it, move it or take it off. A changed day is your own run on the calendar; the
// rest of its week moves around it and the week after adapts.
export default function PlanDetailProvider({ children, today }: { children: React.ReactNode; today: string }) {
  const router = useRouter();
  const ref = useRef<HTMLDialogElement>(null);
  const [view, setView] = useState<View | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (view && !d.open) d.showModal();
    if (!view && d.open) d.close();
  }, [view]);

  const openWeek = useCallback((start: string, draft?: boolean) => setView({ week: mondayOf(start), draft }), []);
  const openDay = useCallback((day: DayDetail) => setView((v) => ({ week: v?.week, draft: v?.draft, day })), []);
  const changed = useCallback(() => {
    setVersion((n) => n + 1);
    router.refresh();
  }, [router]);

  return (
    <PlanDetailContext.Provider value={{ openWeek, openDay, version }}>
      {children}
      <dialog ref={ref} className="sheet" aria-label={view?.day ? "Workout" : "Week"} onClose={() => setView(null)}
        onClick={(e) => { if (e.target === ref.current) setView(null); }}>
        {view?.day ? (
          <DayView key={`${view.day.day}-${version}`} d={view.day} today={today} editable={!view.draft}
            onBack={view.week ? () => setView({ week: view.week, draft: view.draft }) : undefined} onClose={() => setView(null)}
            onChanged={(msg) => { changed(); setView({ week: mondayOf(view.day!.day), note: msg }); }} />
        ) : view?.week ? (
          <WeekView key={`${view.week}-${version}`} start={view.week} draft={view.draft} note={view.note} onDay={openDay}
            onWeek={(start) => setView({ week: start, draft: view.draft })} onClose={() => setView(null)} />
        ) : null}
      </dialog>
    </PlanDetailContext.Provider>
  );
}

function Close({ onClose }: { onClose: () => void }) {
  return <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={onClose} aria-label="Close">Close</button>;
}

// A week day by day. `note` says what a change just did; the week after it may now read adapted.
function WeekView({ start, draft, note, onDay, onWeek, onClose }: {
  start: string; draft?: boolean; note?: string; onDay: (d: DayDetail) => void; onWeek: (start: string) => void; onClose: () => void;
}) {
  const [data, setData] = useState<PlanWeekDays | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`/api/plan/week?start=${start}${draft ? "&draft=true" : ""}`).then(async (res) => {
      if (!live) return;
      if (!res.ok) return setError("Could not load this week.");
      setData(await res.json());
    }).catch(() => live && setError("Could not reach the engine."));
    return () => { live = false; };
  }, [start, draft]);

  const w = data?.week;
  return (
    <div className="sheet-body">
      <div className="sheet-head">
        <div>
          <div className="label">{draft ? "Draft · " : ""}Week of {dayLabel(start, { month: "long", day: "numeric" })}{w ? ` · ${FOCUS_TEXT[w.focus]}` : ""}</div>
          {w ? <div className="mono" style={{ fontSize: 15, marginTop: 4 }}>{mi(w.miles)} mi · {n0(w.gain_ft)} ft up · {n0(w.loss_ft)} ft down · {hours(w.hours)}</div> : null}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 10px" }} onClick={() => onWeek(addDays(start, -7))} aria-label="Week before">‹</button>
          <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 10px" }} onClick={() => onWeek(addDays(start, 7))} aria-label="Week after">›</button>
          <Close onClose={onClose} />
        </div>
      </div>
      {error ? <div className="error-text">{error}</div> : null}
      {!data && !error ? <div className="muted" style={{ display: "flex", gap: 8, alignItems: "center" }}><Spinner label="Loading the week" /> Loading the week</div> : null}
      {note ? <div className="ok-text" role="status">{note} <button type="button" className="link-btn" onClick={() => onWeek(addDays(start, 7))}>See next week</button></div> : null}
      {w ? (
        <>
          {w.adapted ? (
            <div style={{ borderLeft: "3px solid var(--warn-ink)", paddingLeft: 10, fontSize: 14 }}>
              <span style={{ fontWeight: 600 }}>Adapted: </span>{w.adapted.text}.
            </div>
          ) : null}
          {w.coach_note ? <div style={{ fontSize: 14 }}><span style={{ fontWeight: 600 }}>The coach: </span>{w.coach_note}</div> : null}
          {w.workout || w.hill_session ? (
            <div className="muted" style={{ fontSize: 14 }}>
              {[w.workout ? WORKOUT_TEXT[w.workout] : null, w.hill_session ? HILL_TEXT[w.hill_session] : null].filter(Boolean).join(" and ")} this week.
            </div>
          ) : null}
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data!.days.map((d) => {
              const off = d.kind === "rest" || d.kind === "done" || (d.kind === "past" && !d.activities?.length);
              return (
                <button key={d.day} type="button" className="day-row" data-off={off} onClick={() => onDay({ ...d, week_note: w.coach_note })}
                  style={{ borderLeft: `4px solid ${KIND_COLOR[d.kind as SessionKind] ?? "var(--line)"}` }}>
                  <span className="muted" style={{ fontWeight: d.day === data!.date ? 700 : 500 }}>
                    {d.day === data!.date ? "Today" : dayLabel(d.day, { weekday: "short", month: "short", day: "numeric" })}
                  </span>
                  <span style={{ fontWeight: 600 }}>
                    {d.title}{d.own ? <span className="muted" style={{ fontWeight: 400 }}> · {d.kind === "rest" ? "your day off" : "your run"}</span> : null}
                  </span>
                  <span className="mono" style={{ fontSize: 13 }}>{off ? "" : `${mi(d.miles)} mi · ${n0(d.gain_ft)} ft`}</span>
                </button>
              );
            })}
          </div>
          {w.notes.filter((n) => n !== w.adapted?.text).length ? (
            <ul className="muted" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {w.notes.filter((n) => n !== w.adapted?.text).map((n) => <li key={n}>{n}</li>)}
            </ul>
          ) : null}
          <div className="muted">
            {draft ? "A week of the draft: tap a day to see how to run it and why. Accept the plan to change its days." :
              data!.dated ? "Tap a day to see how to run it and why, or to change it." :
              "This week's days take their final shape a week ahead, from what you've done by then; this is how they look from its targets now. Tap a day to see it, or to change it."}
          </div>
        </>
      ) : null}
    </div>
  );
}

type Form = { kind: RunKind; miles: string; gain_ft: string; loss_ft: string; minutes: string; notes: string };

function DayView({ d, today, editable, onBack, onClose, onChanged }: {
  d: DayDetail; today: string; editable: boolean; onBack?: () => void; onClose: () => void; onChanged: (msg?: string) => void;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "move">("view");
  const [form, setForm] = useState<Form>(() => ({
    kind: (PLAN_RUN_KIND[d.kind as SessionKind] ?? d.kind) as RunKind,
    miles: d.miles ? d.miles.toFixed(1) : "", gain_ft: d.gain_ft ? String(Math.round(d.gain_ft)) : "",
    loss_ft: d.loss_ft ? String(Math.round(d.loss_ft)) : "", minutes: "", notes: d.notes ?? "",
  }));
  const [moveTo, setMoveTo] = useState(d.day);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const off = d.kind === "rest" || d.kind === "done";
  const past = d.kind === "past" || d.day < today;
  const canChange = editable && !past && d.kind !== "objective" && d.kind !== "done";
  const adapts = "The rest of the week moves around it and next week adapts.";

  async function call(path: string, method: string, body?: object): Promise<boolean> {
    const res = await fetch(path, { method, headers: { "content-type": "application/json" },
      body: body ? JSON.stringify(body) : undefined }).catch(() => null);
    if (!res?.ok) {
      const b = await res?.json().catch(() => ({}));
      setError(typeof b?.detail === "string" ? b.detail : res ? "Check the numbers." : "Could not reach the engine.");
      return false;
    }
    return true;
  }

  const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(/,/g, "")));
  const runBody = (day: string, f: Form) => ({ day, kind: f.kind, miles: num(f.miles) ?? 0, gain_ft: num(f.gain_ft) ?? 0,
    loss_ft: num(f.loss_ft), minutes: num(f.minutes), notes: f.notes });

  async function act(fn: () => Promise<boolean>, msg: string) {
    setBusy(true);
    setError(null);
    const ok = await fn();
    setBusy(false);
    if (ok) onChanged(msg);
  }

  const save = () => act(() => (d.run_id ? call(`/api/schedule/${d.run_id}`, "PUT", runBody(d.day, form))
    : call("/api/schedule", "POST", runBody(d.day, form))), `Saved ${dayLabel(d.day, { weekday: "long" })}. ${adapts}`);
  const takeOff = () => act(() => (d.run_id ? call(`/api/schedule/${d.run_id}`, "PUT", { day: d.day, kind: "rest", miles: 0 })
    : call("/api/schedule", "POST", { day: d.day, kind: "rest", miles: 0 })), `${dayLabel(d.day, { weekday: "long" })} is a day off. ${adapts}`);
  const reset = () => act(() => call(`/api/schedule/${d.run_id}`, "DELETE"), `${dayLabel(d.day, { weekday: "long" })} is back to the plan's session.`);
  const move = () => act(async () => {
    const body = runBody(moveTo, { ...form, kind: (PLAN_RUN_KIND[d.kind as SessionKind] ?? d.kind) as RunKind });
    if (d.run_id) return call(`/api/schedule/${d.run_id}`, "PUT", body);
    // A plan session moves as your own run on the new day, with the old day off.
    return (await call("/api/schedule", "POST", body)) && call("/api/schedule", "POST", { day: d.day, kind: "rest", miles: 0 });
  }, `Moved to ${dayLabel(moveTo, { weekday: "long", month: "short", day: "numeric" })}. ${adapts}`);

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  const steps = d.steps ?? [];

  return (
    <div className="sheet-body">
      <div className="sheet-head">
        <div>
          <div className="label">
            {d.day === today ? "Today" : dayLabel(d.day, { weekday: "long", month: "long", day: "numeric" })}
            {d.focus && d.focus in FOCUS_TEXT ? ` · ${FOCUS_TEXT[d.focus as keyof typeof FOCUS_TEXT]} week` : ""}
          </div>
          <h2 style={{ margin: "4px 0 0", fontSize: 22, lineHeight: 1.2 }}>
            {d.title}{d.own ? <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}> · {off ? "your day off" : "your run"}</span> : null}
          </h2>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {onBack ? <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} onClick={onBack}>Week</button> : null}
          <Close onClose={onClose} />
        </div>
      </div>

      {!off && d.kind !== "past" ? (
        <div className="mono" style={{ fontSize: 16 }}>
          {mi(d.miles)} mi · {n0(d.gain_ft)} ft up · {n0(d.loss_ft)} ft down{d.minutes ? ` · ${minutesText(d.minutes)}` : ""}
        </div>
      ) : null}

      {d.activities?.length ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {d.activities.map((a) => (
            <Link key={a.id} href={`/activities/${a.id}`}>{a.name ?? "Activity"}: {mi(a.distance_mi)} mi, {n0(a.gain_ft)} ft up</Link>
          ))}
        </div>
      ) : null}

      {steps.length ? (
        <div>
          <div className="field-label" style={{ marginBottom: 4 }}>How to run it</div>
          <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6, fontSize: 15 }}>
            {steps.map((t, i) => <li key={i}>{t}</li>)}
          </ol>
        </div>
      ) : d.how ? <div style={{ fontSize: 15 }}>{d.how}</div> : null}
      {d.notes && d.own ? <div style={{ fontSize: 14 }}><span style={{ fontWeight: 600 }}>Your notes: </span>{d.notes}</div> : null}
      {d.benefit ? <div style={{ fontSize: 14 }}><span style={{ fontWeight: 600 }}>Why: </span>{d.benefit}</div> : null}
      {d.week_note ? <div className="muted" style={{ fontSize: 14 }}><span style={{ fontWeight: 600 }}>This week: </span>{d.week_note}</div> : null}

      {canChange && mode === "edit" ? (
        <div className="form" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
          <div className="form-grid">
            <label className="field"><span className="field-label">Type</span>
              <select value={form.kind} onChange={set("kind")}>
                {(Object.keys(RUN_KIND_TEXT) as RunKind[]).filter((k) => k !== "rest").map((k) => <option key={k} value={k}>{RUN_KIND_TEXT[k]}</option>)}
              </select></label>
            <label className="field"><span className="field-label">Distance, mi</span><input inputMode="decimal" value={form.miles} onChange={set("miles")} /></label>
            <label className="field"><span className="field-label">Gain, ft</span><input inputMode="numeric" value={form.gain_ft} onChange={set("gain_ft")} /></label>
            <label className="field"><span className="field-label">Descent, ft</span><input inputMode="numeric" value={form.loss_ft} onChange={set("loss_ft")} placeholder="Same as gain" /></label>
            <label className="field"><span className="field-label">Time, min</span><input inputMode="numeric" value={form.minutes} onChange={set("minutes")} placeholder="From your pace" /></label>
            <label className="field"><span className="field-label">Notes</span><input value={form.notes} onChange={set("notes")} maxLength={300} /></label>
          </div>
          <div className="actions">
            <button className="btn" disabled={busy} onClick={save} aria-busy={busy}>{busy ? "Saving" : "Save this day"}</button>
            <button className="btn light outline" disabled={busy} onClick={() => setMode("view")}>Cancel</button>
          </div>
        </div>
      ) : null}

      {canChange && mode === "move" ? (
        <div className="form" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
          <label className="field" style={{ maxWidth: 260 }}><span className="field-label">Move it to</span>
            <input type="date" value={moveTo} min={today} onChange={(e) => setMoveTo(e.target.value)} /></label>
          <div className="actions">
            <button className="btn" disabled={busy || !moveTo || moveTo === d.day} onClick={move} aria-busy={busy}>{busy ? "Moving" : "Move"}</button>
            <button className="btn light outline" disabled={busy} onClick={() => setMode("view")}>Cancel</button>
          </div>
        </div>
      ) : null}

      {canChange && mode === "view" ? (
        <div className="actions" style={{ gap: 8 }}>
          <button className="btn" onClick={() => setMode("edit")}>{off ? "Add a run" : "Change this day"}</button>
          {!off ? <button className="btn light outline" onClick={() => setMode("move")}>Move it</button> : null}
          {d.kind !== "rest" ? <button className="btn light outline" disabled={busy} onClick={takeOff}>Take the day off</button> : null}
          {d.run_id ? <button className="btn light outline" disabled={busy} onClick={reset}>Back to the plan</button> : null}
        </div>
      ) : null}
      {canChange && mode === "view" ? (
        <div className="muted">A changed day becomes your own on the calendar. The plan moves the rest of that week around it and eases the next week when the change is big.</div>
      ) : null}
      {error ? <div className="error-text" role="alert">{error}</div> : null}
    </div>
  );
}
