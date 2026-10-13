"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { GOAL_TEXT } from "@/lib/plan";
import type { Goal, Objective } from "@/lib/types";

type Fields = { name: string; day: string; distance_mi: string; gain_ft: string; loss_ft: string;
  longest_descent_ft: string; high_point_ft: string; longest_climb_ft: string };

const num = (v: string) => (v.trim() === "" ? null : Number(v.replace(/,/g, "")));
const str = (v: number | null | undefined, nd = 0) => (v == null ? "" : nd ? v.toFixed(nd) : String(Math.round(v)));

// Add or edit the objective: a race or mountain day with a date (a GPX route fills the numbers in;
// the track itself is not kept), or a goal with no race day, set by weekly targets or a year's totals.
// `next`: where to go after saving (the setup's next step); `submit`: the button's words.
export default function ObjectiveForm({ current, startNew, next, submit }: {
  current: Objective | null; startNew?: boolean; next?: string; submit?: string;
}) {
  const router = useRouter();
  const editing = current && !startNew;
  const [f, setF] = useState<Fields>({
    name: editing ? current.name : "", day: editing ? current.day : "",
    distance_mi: editing ? str(current.distance_mi, 1) : "", gain_ft: editing ? str(current.gain_ft) : "",
    loss_ft: editing ? str(current.loss_ft) : "", longest_descent_ft: editing ? str(current.longest_descent_ft) : "",
    high_point_ft: editing ? str(current.high_point_ft) : "", longest_climb_ft: editing ? str(current.longest_climb_ft) : "",
  });
  const [source, setSource] = useState<"manual" | "gpx">(editing ? current.source : "manual");
  const [goal, setGoal] = useState<Goal>(editing ? current.goal ?? "race" : "race");
  const event = goal === "race" || goal === "trip";
  const year = goal === "target";
  function pickGoal(g: Goal) {
    setGoal(g);
    if (g === "race" || g === "trip") return;
    // A block runs 12 weeks (an ongoing plan 8) from this Monday; a yearly target to Dec 31.
    const now = new Date();
    const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7));
    const end = g === "target" ? new Date(now.getFullYear(), 11, 31)
      : new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + (g === "rolling" ? 8 : 12) * 7 - 1);
    const iso = `${end.getFullYear()}-${String(end.getMonth() + 1).padStart(2, "0")}-${String(end.getDate()).padStart(2, "0")}`;
    setF({ ...f, name: f.name && !Object.values(GOAL_TEXT).includes(f.name) ? f.name : GOAL_TEXT[g], day: iso });
  }
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const set = (k: keyof Fields) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setMessage(null);
    const res = await fetch("/api/plan/route", { method: "POST", body: await file.arrayBuffer(),
      headers: { "content-type": "application/gpx+xml" } }).catch(() => null);
    const body = await res?.json().catch(() => ({}));
    if (!res?.ok) return setMessage({ ok: false, text: body?.detail ?? "Could not read that file." });
    setF({ ...f, name: f.name || body.name || "", distance_mi: str(body.distance_mi, 1), gain_ft: str(body.gain_ft),
      loss_ft: str(body.loss_ft), longest_descent_ft: str(body.longest_descent_ft), high_point_ft: str(body.high_point_ft),
      longest_climb_ft: str(body.longest_climb_ft) });
    setSource("gpx");
    setMessage({ ok: true, text: "Route read. Check the numbers, then save." });
  }

  async function save() {
    setBusy(true);
    setMessage(null);
    const res = await fetch("/api/plan/objective", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: f.name, day: f.day, distance_mi: num(f.distance_mi) ?? 0, gain_ft: num(f.gain_ft) ?? 0,
        loss_ft: event ? num(f.loss_ft) ?? num(f.gain_ft) ?? 0 : year ? num(f.gain_ft) ?? 0 : num(f.loss_ft) ?? num(f.gain_ft) ?? 0,
        longest_descent_ft: event ? num(f.longest_descent_ft) : null, high_point_ft: event ? num(f.high_point_ft) : null,
        longest_climb_ft: event ? num(f.longest_climb_ft) : null, source: event ? source : "manual", goal,
        new: !editing || goal !== (current?.goal ?? "race") }),
    }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      const body = await res?.json().catch(() => ({}));
      const detail = Array.isArray(body?.detail) ? "Check the date and numbers." : body?.detail;
      return setMessage({ ok: false, text: detail ?? "Could not save. Is the engine running?" });
    }
    if (next) return router.push(next);
    setMessage({ ok: true, text: "Saved. A new plan is drafted below for you to review." });
    router.refresh();
  }

  const ready = f.name.trim() && f.day && (event ? num(f.distance_mi) : year ? num(f.distance_mi) || num(f.gain_ft) : num(f.distance_mi));
  return (
    <div className="form">
      <label className="field" style={{ maxWidth: 520 }}>
        <span className="field-label">Training for</span>
        <select value={goal} onChange={(e) => pickGoal(e.target.value as Goal)}>
          {(Object.keys(GOAL_TEXT) as Goal[]).map((g) => <option key={g} value={g}>{GOAL_TEXT[g]}</option>)}
        </select>
      </label>
      {event ? (
        <label className="field" style={{ maxWidth: 520 }}>
          <span className="field-label">Have a GPX of the route? Talus fills in the numbers</span>
          <input type="file" accept=".gpx,application/gpx+xml" onChange={upload} style={{ paddingTop: 10 }} />
        </label>
      ) : (
        <div className="muted" style={{ maxWidth: 640 }}>
          {year ? "Your total for the year: miles, feet of climbing, or both. The plan spreads what's left over the weeks left."
            : goal === "rolling" ? "About where you want a week to sit. The plan looks 8 weeks ahead and redraws itself each Monday."
              : "The weekly miles and vert to build to (or hold). The plan gets there from your last four weeks inside the load limits."}
        </div>
      )}
      <div className="form-grid">
        <label className="field"><span className="field-label">Name</span>
          <input value={f.name} onChange={set("name")} maxLength={80} placeholder={event ? "Fall 50k" : GOAL_TEXT[goal]} /></label>
        <label className="field"><span className="field-label">{event ? "Date" : year ? "By" : "Runs through"}</span>
          <input type="date" value={f.day} onChange={set("day")} /></label>
        <label className="field"><span className="field-label">{event ? "Distance, mi" : year ? "Miles this year" : "Miles a week"}</span>
          <input inputMode="decimal" value={f.distance_mi} onChange={set("distance_mi")} placeholder={event ? "31" : year ? "Optional" : "35"} /></label>
        <label className="field"><span className="field-label">{event ? "Gain, ft" : year ? "Feet up this year" : "Feet up a week"}</span>
          <input inputMode="numeric" value={f.gain_ft} onChange={set("gain_ft")} placeholder={event ? "8,000" : year ? "1,000,000" : "6,000"} /></label>
        {event || !year ? (
          <label className="field"><span className="field-label">{event ? "Descent, ft" : "Feet down a week"}</span>
            <input inputMode="numeric" value={f.loss_ft} onChange={set("loss_ft")} placeholder="Same as gain" /></label>
        ) : null}
      </div>
      {event ? <details>
        <summary className="muted" style={{ cursor: "pointer", minHeight: 24 }}>More detail (optional): longest descent and high point</summary>
        <div className="form-grid" style={{ marginTop: 8 }}>
          <label className="field"><span className="field-label">Longest continuous descent, ft</span>
            <input inputMode="numeric" value={f.longest_descent_ft} onChange={set("longest_descent_ft")} placeholder="Optional" /></label>
          <label className="field"><span className="field-label">High point, ft</span>
            <input inputMode="numeric" value={f.high_point_ft} onChange={set("high_point_ft")} placeholder="Optional" /></label>
        </div>
      </details> : null}
      <div className="actions">
        <button className="btn" disabled={!ready || busy} onClick={save} aria-busy={busy}>
          {busy ? "Saving" : submit ?? (editing ? "Save and redraft the plan" : "Save and build a plan")}
        </button>
        {!ready ? <span className="muted">{event ? "Name, date and distance are needed." : year ? "A name, a date, and miles or feet are needed." : "A name, a date and weekly miles are needed."}</span> : null}
        {message ? <span className={message.ok ? "ok-text" : "error-text"}>{message.text}</span> : null}
      </div>
    </div>
  );
}
