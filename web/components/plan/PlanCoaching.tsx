"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { dayLabel, mi, n0 } from "@/lib/format";
import { addPlanDays, planRequest, type CoachingPlan, type DateAvailability, type RevisionPreview, type RevisionReason, type SessionSnapshot } from "@/lib/plan-coaching";
import { usePlanDetail } from "@/components/plan/PlanDetail";
import WorkoutOutcomes from "@/components/plan/WorkoutOutcomes";
import styles from "./coaching.module.css";

function summary(s: SessionSnapshot) {
  return s.kind === "rest" ? "Rest" : `${s.kind.replaceAll("_", " ")} · ${mi(s.miles)} mi · ${n0(s.gain_ft)} ft up · ${n0(s.loss_ft)} ft down · ${Math.round(s.minutes)} min`;
}

export default function PlanCoaching() {
  const router = useRouter();
  const detail = usePlanDetail();
  const [data, setData] = useState<CoachingPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [reason, setReason] = useState<RevisionReason>("busy");
  const [day, setDay] = useState("");
  const [end, setEnd] = useState("");
  const [minutes, setMinutes] = useState("60");
  const [terrain, setTerrain] = useState<DateAvailability["terrain"]>("any");
  const [locked, setLocked] = useState(false);
  const [remove, setRemove] = useState(false);
  const [preview, setPreview] = useState<RevisionPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const requestBusy = useRef(false);
  const previewHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    planRequest<CoachingPlan>("/api/plan/coaching", "GET", undefined, controller.signal).then((next) => {
      setData(next);
      setDay((d) => d || next.date);
      setEnd((d) => d || next.date);
      setPreview(null);
    }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not read your plan."); });
    return () => controller.abort();
  }, [detail?.version]);

  useEffect(() => { if (preview) previewHeading.current?.focus({ preventScroll: true }); }, [preview]);

  async function run(action: () => Promise<void>) {
    if (requestBusy.current) return;
    requestBusy.current = true;
    setBusy(true); setError(null); setNotice("");
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not reach the engine. Your choices are still here."); }
    finally { requestBusy.current = false; setBusy(false); }
  }
  function edit() { setPreview(null); setNotice(""); }
  async function refresh() {
    setData(await planRequest<CoachingPlan>("/api/plan/coaching"));
    if (detail) detail.invalidate(); else router.refresh();
  }
  function buildPreview() {
    return run(async () => {
      if (!data) return;
      const availability: Record<string, DateAvailability | null> = {};
      if (reason === "availability") {
        if (end < day || end > addPlanDays(day, 59)) throw new Error("Choose a range of up to 60 days.");
        const cap = Number(minutes);
        if (!remove && (!Number.isInteger(cap) || cap < 0 || cap > 1440)) throw new Error("Enter a whole number of minutes from 0 to 1440.");
        for (let d = day; d <= end; d = addPlanDays(d, 1)) availability[d] = remove ? null : { minutes: cap, terrain, locked };
      }
      setPreview(await planRequest<RevisionPreview>("/api/plan/revisions", "POST", {
        reason, day: reason === "availability" && day > addPlanDays(data.date, 13) ? data.date : day, availability,
      }));
    });
  }

  return <section className={`card ${styles.coaching}`} aria-busy={busy}>
    <h2 className="label">Coach plan tools</h2>
    {error ? <div className="error-text" role="alert">{error}</div> : null}
    {notice ? <div role="status">{notice}</div> : null}
    {!data ? <p className="muted">{error ? "Reload the page to try again." : "Reading your plan and recent training…"}</p> : <>
      {data.suggestions.map((s, i) => <div key={`${s.reason}-${i}`} className={styles.suggestion}>
        <span>{s.text}</span><button className="link-btn" disabled={busy} onClick={() => { edit(); setReason(s.reason); setDay(data.date); }}>Review sessions</button>
      </div>)}
      <details>
        <summary>Revise this week or set availability</summary>
        <form onSubmit={(e) => { e.preventDefault(); buildPreview(); }} className={styles.form}>
          <fieldset disabled={busy}>
            <label>What changed?<select value={reason} onChange={(e) => { edit(); setReason(e.target.value as RevisionReason); }}>
              {data.reasons.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select></label>
            <div className={styles.fields}>
              <label>{reason === "availability" ? "From date" : "Session date"}<input type="date" required value={day} min={data.date}
                max={addPlanDays(data.date, reason === "availability" ? 366 : 13)} onChange={(e) => { edit(); setDay(e.target.value); setEnd(e.target.value); }} /></label>
              {reason === "availability" ? <label>Through date<input type="date" required value={end} min={day} max={addPlanDays(data.date, 366)} onChange={(e) => { edit(); setEnd(e.target.value); }} /></label> : null}
            </div>
            {reason === "availability" ? <>
              <label className={styles.check}><input type="checkbox" checked={remove} onChange={(e) => { edit(); setRemove(e.target.checked); }} />Restore recurring availability for these dates</label>
              {!remove ? <>
                <div className={styles.fields}>
                  <label>Available minutes (0 for travel or a day off)<input type="number" min="0" max="1440" step="1" required value={minutes} onChange={(e) => { edit(); setMinutes(e.target.value); }} /></label>
                  <label>Terrain<select value={terrain} onChange={(e) => { edit(); setTerrain(e.target.value as DateAvailability["terrain"]); }}>
                    <option value="any">Any terrain</option><option value="flat">Flat only</option><option value="hills">Hills available</option>
                  </select></label>
                </div>
                <label className={styles.check}><input type="checkbox" checked={locked} onChange={(e) => { edit(); setLocked(e.target.checked); }} />Keep the current session fixed on these dates</label>
              </> : null}
            </> : null}
            <p className="muted">Your own scheduled sessions stay fixed. Changes must fit the time, terrain, recovery spacing and load checks.</p>
            <button className="btn" type="submit">{busy ? "Working…" : "Preview changes"}</button>
          </fieldset>
        </form>
        {preview ? <div className={styles.preview}>
          <h3 ref={previewHeading} tabIndex={-1}>Review the changes</h3>
          {preview.changes.length ? preview.changes.map((c) => <div key={c.day} className={styles.change}>
            <strong>{dayLabel(c.day, { weekday: "long", month: "short", day: "numeric" })}</strong>
            <div className="muted">Before: {summary(c.before)}</div><div>After: {summary(c.after)}</div><p>{c.why}</p>
          </div>) : <p>The next two weeks of sessions stay as shown. Availability changes apply to the dates below.</p>}
          {reason === "availability" ? <p>Availability: {dayLabel(day, { month: "short", day: "numeric" })} to {dayLabel(end, { month: "short", day: "numeric" })} · {remove ? "recurring calendar" : `${minutes} min · ${terrain === "flat" ? "flat only" : terrain === "hills" ? "hills available" : "any terrain"}${locked ? " · fixed" : ""}`}</p> : null}
          <div className="table-wrap"><table><caption>Remaining weekly training</caption><thead><tr><th>Week of</th><th>Before</th><th>After</th></tr></thead>
            <tbody>{preview.weeks.map((w) => <tr key={w.start}><td>{dayLabel(w.start, { month: "short", day: "numeric" })}</td>
              <td>{mi(w.before.miles)} mi · {n0(w.before.gain_ft)} ft up · {n0(w.before.loss_ft)} ft down</td>
              <td>{mi(w.after.miles)} mi · {n0(w.after.gain_ft)} ft up · {n0(w.after.loss_ft)} ft down</td></tr>)}</tbody></table></div>
          <p>{preview.impact}</p>
          <div className="actions"><button className="btn" disabled={busy} onClick={() => run(async () => {
            await planRequest(`/api/plan/revisions/${preview.id}/accept`, "POST"); setPreview(null); await refresh(); setNotice("Plan changes accepted. You can undo this revision below.");
          })}>Accept changes</button><button className="btn light outline" disabled={busy} onClick={() => setPreview(null)}>Discard preview</button></div>
        </div> : null}
      </details>
      {Object.keys(data.availability).length ? <details><summary>Saved availability and fixed dates</summary>
        <ul>{Object.entries(data.availability).sort(([a], [b]) => a.localeCompare(b)).map(([d, a]) => <li key={d}>
          {dayLabel(d, { month: "short", day: "numeric" })}: {a.minutes} min · {a.terrain}{a.locked ? " · fixed" : ""}
        </li>)}</ul>
      </details> : null}
      {data.undo_id ? <button className="link-btn" disabled={busy} onClick={() => run(async () => {
        await planRequest(`/api/plan/revisions/${data.undo_id}/undo`, "POST"); setPreview(null); await refresh(); setNotice("The last plan revision was undone.");
      })}>Undo last accepted revision</button> : null}
      <WorkoutOutcomes data={data} onSaved={async () => { await refresh(); setNotice("Workout feedback saved."); }} />
      <details open={data.evidence.label === "Limited evidence"}>
        <summary>Plan evidence · {data.evidence.label}</summary>
        <p>{data.evidence.activity_days} activity days in the last eight weeks. Latest activity: {data.evidence.last_activity ? dayLabel(data.evidence.last_activity, { month: "short", day: "numeric" }) : "unavailable"}.</p>
        {data.evidence.missing.length ? <ul>{data.evidence.missing.map((m) => <li key={m}>{m}</li>)}</ul> : null}
        <ul>{data.evidence.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        {data.evidence.milestone ? <p><strong>Next milestone: {dayLabel(data.evidence.milestone.day, { weekday: "long", month: "short", day: "numeric" })}.</strong> {data.evidence.milestone.text}</p> : null}
      </details>
    </>}
  </section>;
}
