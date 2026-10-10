"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import AiCost from "@/components/AiCost";
import { Dots } from "@/components/Loading";
import type { Talk, TalkGroup, TalkMessage } from "@/lib/types";

type Picks = Record<string, string | string[]>;

const PLACEHOLDER: Record<string, string> = {
  objective: "Baldy 50k on Jan 2, 31 mi, 8,000 ft",
  draft: "Tell me what to change",
  stuck: "Or tell me what to change",
};

function startPicks(groups: TalkGroup[]): Picks {
  const out: Picks = {};
  for (const g of groups) if (g.value) out[g.key] = g.value;
  return out;
}

// Planning with the coach: a few questions, mostly answered with a tap, that fill the same inputs
// as the plan form. The engine drafts every number; after the draft, ask for changes in your own words.
export default function PlanTalk({ initial }: { initial: Talk }) {
  const router = useRouter();
  const [talk, setTalk] = useState(initial);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const last = [...talk.messages].reverse().find((m) => m.role === "coach");
  const groups = last?.groups ?? [];
  const [picks, setPicks] = useState<Picks>(() => startPicks(groups));
  const list = useRef<HTMLDivElement>(null);

  // Keep the newest message in view inside the conversation, without scrolling the page.
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [talk.messages.length, busy]);

  async function send(body: object, pending: string, optimistic?: string) {
    setBusy(pending);
    setError(null);
    const before = talk;
    if (optimistic) setTalk({ ...talk, messages: [...talk.messages, { role: "athlete", text: optimistic }] });
    const res = await fetch("/api/plan/talk", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body) }).catch(() => null);
    setBusy(null);
    if (!res?.ok) {
      setTalk(before);
      const detail = res ? (await res.json().catch(() => ({}))).detail : null;
      return setError(typeof detail === "string" ? detail : "Could not reach the coach. Is the engine running?");
    }
    const next: Talk = await res.json();
    setTalk(next);
    const coachLast = [...next.messages].reverse().find((m) => m.role === "coach");
    setPicks(startPicks(coachLast?.groups ?? []));
    setText("");
    if (next.step === "done") return router.push("/plan");
    router.refresh();
  }

  function submitText(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    send({ text: t, answers: groups.length ? picks : undefined },
      talk.step === "objective" || talk.step === "draft" ? "The coach is reading that." : "One moment.", t);
  }

  function tap(g: TalkGroup, option: string) {
    if (g.multi) {
      const now = (picks[g.key] as string[] | undefined) ?? [];
      return setPicks({ ...picks, [g.key]: now.includes(option) ? now.filter((o) => o !== option) : [...now, option] });
    }
    const next = { ...picks, [g.key]: option };
    setPicks(next);
    // A question with a single one-tap answer goes straight on.
    if (groups.length === 1) send({ answers: next }, talk.step === "body" ? "Drafting your plan." : "One moment.");
  }

  async function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy("Reading the route.");
    setError(null);
    const res = await fetch("/api/plan/route", { method: "POST", body: await file.arrayBuffer(),
      headers: { "content-type": "application/gpx+xml" } }).catch(() => null);
    if (!res?.ok) {
      setBusy(null);
      const detail = res ? (await res.json().catch(() => ({}))).detail : null;
      return setError(typeof detail === "string" ? detail : "Could not read that GPX file.");
    }
    const route = await res.json();
    if (!route.name) route.name = file.name.replace(/\.gpx$/i, "");
    send({ route }, "Reading the route.");
  }

  async function restart() {
    const res = await fetch("/api/plan/talk/restart", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode: talk.mode }) }).catch(() => null);
    if (!res?.ok) return setError("Could not start over. Is the engine running?");
    const next: Talk = await res.json();
    setTalk(next);
    setPicks(startPicks(next.messages[next.messages.length - 1]?.groups ?? []));
    setError(null);
  }

  const multiStep = groups.length > 1 || groups.some((g) => g.multi);
  const lastIndex = talk.messages.lastIndexOf(last as TalkMessage);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div ref={list} style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 640, overflowY: "auto" }} aria-live="polite">
        {talk.messages.map((m, i) => {
          const active = i === lastIndex && !busy;
          return (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: m.role === "athlete" ? "flex-end" : "flex-start" }}>
              <div style={{
                maxWidth: "min(620px, 92%)", background: m.role === "athlete" ? "var(--strong)" : "var(--bg)",
                color: m.role === "athlete" ? "var(--on-strong)" : "var(--ink)", borderRadius: 14, padding: "10px 14px", fontSize: 15,
                whiteSpace: "pre-wrap", opacity: m.role === "coach" && i < lastIndex - 1 ? 0.85 : 1,
              }}>
                {m.text}
                {m.cost ? <span className="muted"><AiCost cost={m.cost} /></span> : null}
              </div>
              {active && m.groups?.length ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: "100%" }}>
                  {m.groups.map((g) => (
                    <div key={g.key} role="group" aria-label={g.label ?? "Answers"} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {g.label ? <span className="field-label">{g.label}{g.multi ? " (pick any)" : ""}</span> : null}
                      <div className="chips">
                        {g.options.map((o) => {
                          const v = picks[g.key];
                          const on = Array.isArray(v) ? v.includes(o) : v === o;
                          return <button key={o} type="button" className="chip" aria-pressed={on} onClick={() => tap(g, o)}>{o}</button>;
                        })}
                      </div>
                    </div>
                  ))}
                  {multiStep ? (
                    <div><button className="btn" type="button" onClick={() => send({ answers: picks }, "One moment.")}>Next</button></div>
                  ) : null}
                </div>
              ) : null}
              {active && m.actions?.length ? (
                <div className="actions">
                  {m.actions.map((a, k) => (
                    <button key={a.id} type="button" className={k === 0 ? "btn" : "btn light outline"}
                      onClick={() => send({ action: a.id }, a.id === "accept" ? "Putting it on your calendar." : "One moment.")}>{a.label}</button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
        {busy ? (
          <div role="status" style={{ alignSelf: "flex-start", display: "flex", alignItems: "center", gap: 10, background: "var(--bg)", borderRadius: 14,
            padding: "10px 14px", fontSize: 14, color: "var(--muted)" }}>
            <Dots /><span>{busy}</span>
          </div>
        ) : null}
      </div>
      <form onSubmit={submitText} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <label className="visually-hidden" htmlFor="talk-text">Your answer</label>
        <input id="talk-text" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} disabled={!!busy}
          placeholder={PLACEHOLDER[talk.step] ?? "Or type your answer"}
          style={{ flex: "1 1 240px", minWidth: 0, minHeight: 44, border: "1px solid var(--line)", borderRadius: 10, padding: "0 12px", font: "inherit", color: "var(--ink)", background: "var(--field)" }} />
        <button className="btn" disabled={!!busy || !text.trim()}>Send</button>
        {talk.step === "objective" ? (
          <label className="btn light outline" style={{ cursor: "pointer" }}>
            Upload GPX
            <input type="file" accept=".gpx,application/gpx+xml" onChange={upload} className="visually-hidden" />
          </label>
        ) : null}
      </form>
      {error ? <span className="error-text">{error}</span> : null}
      <div className="muted" style={{ fontSize: 13, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <span>
          The engine drafts every number from your training.
          {talk.reads_with_ai ? " Typed answers are read by the AI coach; taps cost nothing." : ""}
        </span>
        <button type="button" className="link-btn" style={{ fontSize: 13 }} onClick={restart}>Start over</button>
      </div>
    </div>
  );
}
