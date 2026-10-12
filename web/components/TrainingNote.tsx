"use client";

import { useState } from "react";
import AiCost from "@/components/AiCost";
import { Dots } from "@/components/Loading";
import type { AiCost as Cost } from "@/lib/types";

const QUESTIONS = [
  { key: "training_for", label: "What are you training for?", placeholder: "A spring 50k, a big Sierra day, general mountain fitness" },
  { key: "wants", label: "What kind of training do you want?", placeholder: "More vert, one long day a week, some speed work" },
  { key: "avoid", label: "Anything to avoid or work around?", placeholder: "Flat roads, a cranky knee, no runs before work on Mondays" },
] as const;
type Key = (typeof QUESTIONS)[number]["key"];

const field: React.CSSProperties = {
  width: "100%", border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px", font: "inherit",
  color: "var(--ink)", background: "var(--field)",
};

// The athlete's own words on the training they want. Every coach answer reads it. The coach can
// draft it from three short answers and recent training; nothing is saved until Save.
export default function TrainingNote({ initial }: { initial: string }) {
  const [saved, setSaved] = useState(initial);
  const [note, setNote] = useState(initial);
  const [editing, setEditing] = useState(!initial);
  const [asking, setAsking] = useState(false);
  const [answers, setAnswers] = useState<Record<Key, string>>({ training_for: "", wants: "", avoid: "" });
  const [busy, setBusy] = useState<"draft" | "save" | null>(null);
  const [cost, setCost] = useState<Cost | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function draft(e: React.FormEvent) {
    e.preventDefault();
    setBusy("draft");
    setMessage(null);
    const res = await fetch("/api/coach/note/draft", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(answers) }).catch(() => null);
    setBusy(null);
    if (!res?.ok) return setMessage("Could not draft the note. Is the engine running?");
    const out = await res.json();
    setNote(out.note);
    setCost(out.cost);
    setAsking(false);
    setEditing(true);
    setMessage(out.source === "coach" ? "Drafted from your answers and your recent weeks. Edit it, then save."
      : "Written from your answers (the AI coach isn't available). Edit it, then save.");
  }

  async function save() {
    setBusy("save");
    const res = await fetch("/api/coach/note", { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ note }) }).catch(() => null);
    setBusy(null);
    if (!res?.ok) return setMessage("Could not save the note.");
    setSaved(note.trim());
    setEditing(false);
    setMessage("Saved. The coach reads this before every answer.");
  }

  const filled = Object.values(answers).some((v) => v.trim());
  return (
    <section className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Your training goals</h2>
        {!editing && !asking ? <button className="link-btn" onClick={() => { setEditing(true); setMessage(null); }}>Edit</button> : null}
      </div>
      {!editing && !asking ? (
        <div style={{ whiteSpace: "pre-wrap" }}>{saved}</div>
      ) : null}
      {editing && !asking ? (
        <>
          <div className="muted" style={{ fontSize: 14 }}>
            Tell the coach what kind of training you want. Every brief, activity read and chat answer follows it.
          </div>
          <label className="visually-hidden" htmlFor="coach-note">Training goals</label>
          <textarea id="coach-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1500} rows={4}
            placeholder="For example: I'm building for a spring 50k with lots of descent. I want one long day a week and more vert, and I'd rather hike steep climbs than run them."
            style={{ ...field, resize: "vertical", minHeight: 96 }} />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn" onClick={save} disabled={busy !== null || note.trim() === saved} aria-busy={busy === "save"}>Save</button>
            <button className="btn light outline" onClick={() => { setAsking(true); setMessage(null); }} disabled={busy !== null}>
              Draft with AI
            </button>
            {saved ? <button className="link-btn" onClick={() => { setNote(saved); setEditing(false); setMessage(null); }}>Cancel</button> : null}
          </div>
        </>
      ) : null}
      {asking ? (
        <form onSubmit={draft} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="muted" style={{ fontSize: 14 }}>Answer any of these. The coach adds what your recent training shows.</div>
          {QUESTIONS.map((q) => (
            <label key={q.key} style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 600 }}>
              {q.label}
              <input value={answers[q.key]} maxLength={300} placeholder={q.placeholder} style={{ ...field, fontWeight: 400, minHeight: 44 }}
                onChange={(e) => setAnswers({ ...answers, [q.key]: e.target.value })} />
            </label>
          ))}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn" disabled={busy !== null || !filled} aria-busy={busy === "draft"}>Draft my note</button>
            <button type="button" className="link-btn" onClick={() => setAsking(false)} disabled={busy !== null}>Back</button>
            {busy === "draft" ? <span className="muted" style={{ display: "inline-flex", gap: 8, alignItems: "center", fontSize: 13 }}><Dots />Reading your recent weeks</span> : null}
          </div>
        </form>
      ) : null}
      {message ? <div className="muted" style={{ fontSize: 13 }}>{message}<AiCost cost={cost} /></div> : null}
    </section>
  );
}
