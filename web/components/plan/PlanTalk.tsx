"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import AiCost from "@/components/AiCost";
import CoachText from "@/components/CoachText";
import { Dots } from "@/components/Loading";
import type { Talk, TalkGroup } from "@/lib/types";

type Picks = Record<string, string | string[]>;
const PHASES = ["Your goal", "Your schedule", "Your training", "Review"];
const PHASE: Record<string, number> = { objective: 0, goal: 0, days: 1, time: 1, terrain: 1, workouts: 2, body: 2, confirm: 3, draft: 3, stuck: 3, done: 3 };
const PLACEHOLDER: Record<string, string> = {
  objective: "Race name, date, distance and climbing…",
  goal: "Tell me what a good day would look like…",
  days: "For example: weekdays except Wednesday, long run Sunday",
  time: "For example: 1 hour on weekdays, 4 hours on weekends",
  terrain: "Describe the hills or trails you can train on…",
  workouts: "Or name workouts, like tempo and hill sprints…",
  body: "Add anything about pain, recovery or how you feel…",
  confirm: "Add travel dates, a tune-up race or anything else…",
  draft: "Ask about the draft, or tell me what to change…",
  stuck: "Tell me what to change…",
};

function startPicks(groups: TalkGroup[]): Picks {
  // Empty multi-selects are answers too: sending no keys makes "no workouts" impossible.
  return Object.fromEntries(groups.filter((g) => g.value != null || g.multi)
    .map((g) => [g.key, g.value ?? []]));
}

async function readTalk(res: Response): Promise<Talk> {
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "The coach couldn't answer. Your answer is still here; try again.");
  if (!data || !Array.isArray(data.messages) || typeof data.step !== "string") {
    throw new Error("The coach's reply couldn't be read. Your answer is still here; try again.");
  }
  return data as Talk;
}

export default function PlanTalk({ initial, draftReady = false }: { initial: Talk; draftReady?: boolean }) {
  const router = useRouter();
  const [talk, setTalk] = useState(initial);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState(false);
  const [confirmRestart, setConfirmRestart] = useState(false);
  const requestBusy = useRef(false);
  const list = useRef<HTMLDivElement>(null);
  const lastIndex = talk.messages.map((m) => m.role).lastIndexOf("coach");
  const groups = talk.messages[lastIndex]?.groups ?? [];
  const [picks, setPicks] = useState<Picks>(() => startPicks(groups));
  const locked = !!busy || !!talk.building;
  const phase = PHASE[talk.step] ?? 0;

  // A response from a poll started before an answer must never replace that answer.
  useEffect(() => {
    if (!talk.building || busy) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const next = await readTalk(await fetch(`/api/plan/talk?mode=${talk.mode}`, { signal: controller.signal }));
        if (controller.signal.aborted) return;
        setPollError(false);
        setTalk(next);
        if (!next.building) {
          setPicks(startPicks([...next.messages].reverse().find((m) => m.role === "coach")?.groups ?? []));
          router.refresh();
        }
      } catch {
        if (controller.signal.aborted) return;
        setPollError(true);
        setTalk({ ...talk }); // retry on the next tick
      }
    }, 4000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [talk, busy, router]);

  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [talk.messages.length, busy, draftReady]);

  // Serialize text, chips, uploads and restart, including response parsing.
  async function run(pending: string, request: () => Promise<Talk>, optimistic?: string) {
    if (requestBusy.current || talk.building) return;
    requestBusy.current = true;
    setBusy(pending);
    setError(null);
    setConfirmRestart(false);
    const before = talk;
    if (optimistic) setTalk({ ...talk, messages: [...talk.messages, { role: "athlete", text: optimistic }] });
    try {
      const next = await request();
      setTalk(next);
      setPicks(startPicks([...next.messages].reverse().find((m) => m.role === "coach")?.groups ?? []));
      setText("");
      if (next.step === "done") router.push("/plan");
      router.refresh();
    } catch (e) {
      setTalk(before);
      setError(e instanceof TypeError ? "Could not reach the coach. Your answer is still here; try again."
        : e instanceof Error ? e.message : "Could not read the reply. Try again.");
    } finally {
      requestBusy.current = false;
      setBusy(null);
    }
  }

  function send(body: object, pending: string, optimistic?: string) {
    return run(pending, async () => readTalk(await fetch("/api/plan/talk", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    })), optimistic);
  }

  function submitText(e: React.FormEvent) {
    e.preventDefault();
    const answer = text.trim();
    if (!answer) return;
    send({ text: answer, answers: groups.length ? picks : undefined },
      talk.step === "confirm" ? "Drafting your plan." : "The coach is reading that.", answer);
  }

  function sendAnswers(answers: Picks) {
    const detail = text.trim();
    return send({ answers, text: detail || undefined },
      talk.step === "confirm" ? "Drafting your plan." : "Saving your answer.", detail || undefined);
  }

  function tap(g: TalkGroup, option: string) {
    if (locked || requestBusy.current) return;
    const next = { ...picks };
    if (g.multi) {
      const now = Array.isArray(picks[g.key]) ? picks[g.key] as string[] : [];
      const selected = now.includes(option) ? now.filter((o) => o !== option) : [...now, option];
      next[g.key] = selected;
      if (g.key === "days" && !selected.includes(String(next.long_day))) delete next.long_day;
    } else {
      next[g.key] = option;
      // Make the relationship visible instead of silently adding a training day on the server.
      if (g.key === "long_day") next.days = Array.from(new Set([...(Array.isArray(next.days) ? next.days : []), option]));
    }
    setPicks(next);
    if (!g.multi && groups.length === 1) sendAnswers(next);
  }

  function upload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    run("Reading the route.", async () => {
      const res = await fetch("/api/plan/route", { method: "POST", body: await file.arrayBuffer(), headers: { "content-type": "application/gpx+xml" } });
      const route = await res.json().catch(() => null);
      if (!res.ok || !route) throw new Error(typeof route?.detail === "string" ? route.detail : "Could not read that GPX file. Try another route.");
      if (!route.name) route.name = file.name.replace(/\.gpx$/i, "");
      return readTalk(await fetch("/api/plan/talk", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ route }) }));
    });
  }

  const multiStep = groups.length > 1 || groups.some((g) => g.multi);
  const needsDays = talk.step === "days" && (!Array.isArray(picks.days) || !picks.days.length || !picks.long_day);
  const needsTime = talk.step === "time" && (!picks.weekday || !picks.weekend);
  return (
    <div className="plan-talk">
      {talk.mode === "setup" ? <ol className="plan-talk-progress" aria-label="Plan setup progress">
        {PHASES.map((label, i) => <li key={label} aria-current={i === phase ? "step" : undefined} data-complete={i < phase}>
          <span aria-hidden="true">{i < phase ? "✓" : i + 1}</span>{label}
        </li>)}
      </ol> : null}
      <div className="plan-talk-heading">
        <div>
          <h2>{talk.building ? "Building your draft" : talk.step === "draft" ? "Let’s refine your plan" : talk.step === "confirm" ? "Check your answers" : talk.mode === "change" ? "Adjust your plan" : "A plan that fits your life"}</h2>
          <p className="muted">{talk.mode === "change" ? "Review a new draft before replacing your current plan." : "Start with your goal. We’ll fit the training around your days, terrain and recovery."}</p>
        </div>
        {draftReady ? <a href="#plan-draft" className="link-btn">See draft ↓</a> : null}
      </div>
      <div ref={list} className="plan-talk-log" role="log" aria-label="Planning conversation" aria-busy={!!busy}>
        {talk.messages.map((m, i) => {
          const active = i === lastIndex;
          return (
            <div key={i} className="plan-talk-message" data-role={m.role}>
              <div className="plan-talk-bubble">
                <span className="plan-talk-speaker">{m.role === "coach" ? "Coach" : "You"}</span>
                {m.role === "coach" ? <CoachText text={m.text} /> : <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>}
                {m.cost ? <span className="muted"><AiCost cost={m.cost} /></span> : null}
              </div>
              {active && m.groups?.length ? <div className="plan-talk-answers">
                {m.groups.map((g) => <div key={g.key} role="group" aria-label={g.label ?? (g.key === "days" ? "Training days" : "Answers")} className="plan-talk-group">
                  <span className="field-label">{g.label ?? (g.key === "days" ? "Training days" : "Choose an answer")}{g.multi ? <span className="muted"> · choose any</span> : null}</span>
                  <div className="chips">
                    {g.options.map((o) => {
                      const v = picks[g.key];
                      return <button key={o} type="button" className="chip" disabled={locked} aria-pressed={Array.isArray(v) ? v.includes(o) : v === o} onClick={() => tap(g, o)}>{o}</button>;
                    })}
                  </div>
                </div>)}
                {multiStep ? <div className="actions">
                  <button className="btn" type="button" disabled={locked || needsDays || needsTime} onClick={() => sendAnswers(picks)}>Continue</button>
                  <span className="muted" style={{ fontSize: 13 }}>{needsDays ? "Choose training days and a long day." : needsTime ? "Choose a time limit for each." : talk.step === "workouts" ? "Keep the picks, change them, or clear them all." : "Or add more detail below."}</span>
                </div> : null}
              </div> : null}
              {active && m.actions?.length ? <div className="actions">
                {m.actions.map((a, k) => a.id === "accept" ? (draftReady ? <a key={a.id} className="btn" href="#plan-draft">Review &amp; accept draft</a>
                  : <button key={a.id} type="button" className="btn" disabled>Loading draft…</button>) :
                  <button key={a.id} type="button" disabled={locked} className={k === 0 ? "btn" : "btn light outline"}
                    onClick={() => send({ action: a.id }, "One moment.")}>{a.label}</button>)}
              </div> : null}
            </div>
          );
        })}
      </div>
      {locked ? <div role="status" className="plan-talk-status"><Dots /><span>{busy ?? "The coach is drafting and checking your plan. This can take a few minutes."}</span></div> : null}
      {pollError ? <p className="muted" role="status">Connection interrupted. Checking again automatically; your draft is saved on the server.</p> : null}
      <form onSubmit={submitText} className="plan-talk-compose">
        <label className="field-label" htmlFor="talk-text">{talk.step === "draft" ? "Ask or adjust" : "Your answer or extra detail"}</label>
        <textarea id="talk-text" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} disabled={locked} rows={2}
          placeholder={PLACEHOLDER[talk.step] ?? "Or type your answer…"} aria-describedby={error ? "talk-error" : undefined} />
        <div className="actions">
          <button className="btn" disabled={locked || !text.trim()}>Send answer</button>
          {talk.step === "objective" ? <label className="btn light outline plan-talk-upload" aria-disabled={locked}>
            Upload GPX<input type="file" accept=".gpx,application/gpx+xml" onChange={upload} disabled={locked} className="visually-hidden" />
          </label> : null}
          <span className="muted" style={{ fontSize: 12, marginLeft: "auto" }}>{text.length}/1,000</span>
        </div>
      </form>
      {error ? <p id="talk-error" className="error-text" role="alert">{error}</p> : null}
      <div className="plan-talk-footer muted">
        <span>Submitted answers are saved as you go. {talk.reads_with_ai ? "The coach reads typed answers; taps cost nothing." : "Tap an answer or write in your own words."}</span>
        <button type="button" className="link-btn" disabled={locked} onClick={() => setConfirmRestart(true)}>Start over</button>
      </div>
      {confirmRestart ? <div className="plan-talk-restart" role="group" aria-label="Restart planning">
        <p>Start a fresh conversation? This clears your conversation answers. Your saved goal and any existing plan stay in place.</p>
        <div className="actions"><button type="button" className="btn light outline" onClick={() => setConfirmRestart(false)}>Keep going</button>
          <button type="button" className="btn" onClick={() => run("Starting a fresh conversation.", async () => readTalk(await fetch("/api/plan/talk/restart", {
            method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ mode: talk.mode }),
          })))}>Start fresh</button></div>
      </div> : null}
    </div>
  );
}
