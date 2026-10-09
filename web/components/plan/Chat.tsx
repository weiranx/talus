"use client";

import { useEffect, useRef, useState } from "react";
import CoachFeedback from "@/components/CoachFeedback";
import type { ChatMessage } from "@/lib/types";

// Ask the coach. Every number in an answer is checked against your data before it shows.
export default function Chat({ initial }: { initial: ChatMessage[] }) {
  const [messages, setMessages] = useState(initial);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [messages]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    setMessages([...messages, { id: -1, created_at: "", role: "athlete", text: q, source: null, log_id: null }]);
    setQuestion("");
    const res = await fetch("/api/coach/chat", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ question: q }) }).catch(() => null);
    setBusy(false);
    if (!res?.ok) {
      setMessages(messages);
      setQuestion(q);
      return setError("Could not reach the coach. Is the engine running?");
    }
    setMessages((await res.json()).messages);
  }

  async function clear() {
    const res = await fetch("/api/coach/chat", { method: "DELETE" }).catch(() => null);
    if (res?.ok) setMessages([]);
  }

  return (
    <section className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 className="label">Ask the coach</h2>
        {messages.length ? <button className="link-btn" onClick={clear}>Clear</button> : null}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 420, overflowY: "auto" }}>
        {messages.length === 0 ? (
          <div className="muted">Ask about your plan, your loads or your objective. For example: can I swap Saturday&apos;s long day to Sunday?</div>
        ) : null}
        {messages.map((m, i) => (
          <div key={`${m.id}-${i}`} style={{
            alignSelf: m.role === "athlete" ? "flex-end" : "flex-start", maxWidth: "88%",
            background: m.role === "athlete" ? "var(--ink)" : "var(--bg)", color: m.role === "athlete" ? "#fff" : "var(--ink)",
            borderRadius: 12, padding: "8px 12px", fontSize: 14, display: "flex", flexDirection: "column", gap: 6,
          }}>
            <div style={{ whiteSpace: "pre-wrap" }}>{m.text}</div>
            {m.role === "coach" && m.source === "coach" && m.log_id ? (
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <span className="muted" style={{ fontSize: 11 }}>AI coach, numbers checked</span>
                <CoachFeedback logId={m.log_id} initial={null} />
              </div>
            ) : null}
          </div>
        ))}
        {busy ? <div className="muted">The coach is reading your numbers.</div> : null}
        <div ref={end} />
      </div>
      <form onSubmit={ask} style={{ display: "flex", gap: 8 }}>
        <label className="visually-hidden" htmlFor="chat-q">Question</label>
        <input id="chat-q" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000}
          placeholder="Ask a question" style={{ flex: 1, minWidth: 0, minHeight: 44, border: "1px solid var(--line)", borderRadius: 10, padding: "0 12px", font: "inherit" }} />
        <button className="btn" disabled={busy || !question.trim()}>Ask</button>
      </form>
      {error ? <span className="error-text">{error}</span> : null}
    </section>
  );
}
