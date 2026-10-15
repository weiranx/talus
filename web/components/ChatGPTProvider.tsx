"use client";

import { useEffect, useState } from "react";
import type { Settings } from "@/lib/types";

type Connection = NonNullable<Settings["coach"]["chatgpt"]>;
export default function ChatGPTProvider({ initial, model, onModel }: {
  initial: Connection; model: string; onModel: (id: string) => void;
}) {
  const [connection, setConnection] = useState(initial);
  const [models, setModels] = useState<{ id: string; label: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!connection.sharing || !connection.available) return;
    const abort = new AbortController();
    fetch("/api/settings/chatgpt/models", { signal: abort.signal })
      .then(async r => { const data = await r.json(); if (!r.ok) throw new Error(data.detail); return data; })
      .then(data => setModels(data.models))
      .catch(e => { if (!abort.signal.aborted) setError(e.message || "Could not load models."); });
    return () => abort.abort();
  }, [connection.sharing, connection.available, connection.email, connection.pending]);
  useEffect(() => {
    if (!connection.pending) return;
    let disposed = false;
    const timer = setInterval(async () => {
      try {
        const r = await fetch("/api/settings/chatgpt");
        if (!r.ok) throw new Error("Could not check ChatGPT connection.");
        const data: Connection = await r.json();
        if (!disposed) { setConnection(data); if (data.error) setError(data.error); }
      } catch { if (!disposed) setError("Could not check ChatGPT connection. Refresh Settings to try again."); }
    }, 2000);
    return () => { disposed = true; clearInterval(timer); };
  }, [connection.pending]);

  async function connect() {
    // Open during the click so browser popup protection doesn't block the sign-in tab.
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setBusy(true); setError("");
    try {
      if (!popup) throw new Error("Allow popups for Talus, then try again.");
      const r = await fetch("/api/settings/chatgpt/connect", { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Could not start ChatGPT sign-in.");
      popup.location.href = data.url;
      setConnection(c => ({ ...c, pending: true }));
    } catch (e) { popup?.close(); setError(e instanceof Error ? e.message : "Could not connect."); }
    finally { setBusy(false); }
  }
  async function disconnect() {
    setBusy(true); setError("");
    try {
      const r = await fetch("/api/settings/chatgpt", { method: "DELETE" });
      if (!r.ok) throw new Error("Could not disconnect ChatGPT.");
      setConnection(c => ({ ...c, connected: false, sharing: false, pending: false, email: null }));
      setModels([]); onModel("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not disconnect."); }
    finally { setBusy(false); }
  }
  return <div className="stack">
    {!connection.available ? <p className="muted">ChatGPT sign-in needs Talus running on this computer. Set TALUS_CHATGPT_LOCAL=1 on the local engine and restart it. Hosted connections require OpenAI approval.</p> : <>
      <div className="muted">{connection.connected ? `Connected${connection.email ? ` as ${connection.email}` : ""}. ${connection.sharing ? "Using ChatGPT plan." : "Reconnect and approve ChatGPT plan usage to enable AI requests."}` : "Use an eligible ChatGPT plan for your AI coach. Your training context is sent to OpenAI when the coach runs."}</div>
      <div className="actions">
        <button className="btn" type="button" onClick={connect} disabled={busy || connection.pending}>{connection.pending ? "Waiting for ChatGPT…" : "Continue with ChatGPT"}</button>
        {(connection.connected || connection.pending) && <button className="btn" type="button" onClick={disconnect} disabled={busy}>Disconnect</button>}
        <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage</a>
      </div>
      {connection.sharing && <label className="field"><span className="field-label">ChatGPT model</span>
        <select value={model} onChange={e => onModel(e.target.value)} disabled={busy || !models.length}>
          <option value="">Choose a model</option>
          {models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select><span className="muted">Save coach settings to use this model. ChatGPT plan limits apply.</span>
      </label>}
    </>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </div>;
}
