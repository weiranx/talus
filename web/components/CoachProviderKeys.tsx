"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import type { CoachProviderStatus } from "@/lib/types";

function ProviderKey({ initial }: { initial: CoachProviderStatus }) {
  const router = useRouter();
  const [provider, setProvider] = useState(initial);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function update(method: "PUT" | "DELETE") {
    setBusy(true);
    setMessage(null);
    try {
      const response = await fetch(`/api/admin/coach-keys/${provider.id}`, {
        method,
        ...(method === "PUT" ? { headers: { "content-type": "application/json" }, body: JSON.stringify({ api_key: key.trim() }) } : {}),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data) {
        setMessage({ ok: false, text: typeof data?.detail === "string" ? data.detail : "Could not update the API key." });
        return;
      }
      setKey("");
      setProvider(data as CoachProviderStatus);
      setMessage({ ok: true, text: method === "PUT" ? "Key saved. New requests use it immediately." : data.key_set ? "Saved key removed. Using the server environment key." : "Saved key removed. This provider has no API key configured." });
      router.refresh();
    } catch {
      setMessage({ ok: false, text: "Could not reach the engine. Try again." });
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (key.trim()) void update("PUT");
  }

  const helpId = `coach-key-help-${provider.id}`;
  return (
    <form onSubmit={submit}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <label className="field">
          <span className="field-label">{provider.label} API key</span>
          <span className="muted">{provider.source === "admin" ? "Using the key saved in Admin." : provider.source === "environment" ? "Using the server environment key." : "No API key configured."}</span>
          <input type="password" value={key} onChange={event => setKey(event.target.value)}
            autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={1024}
            placeholder="Enter a new API key" aria-describedby={helpId} />
          <span id={helpId} className="muted">Enter a replacement to change it. The saved key is never shown.</span>
        </label>
        <div className="actions">
          <button className="btn" type="submit" disabled={!key.trim()} aria-busy={busy}>{busy ? "Updating…" : `Save ${provider.label} key`}</button>
          {provider.saved_key_set ? <button className="btn light outline" type="button" onClick={() => void update("DELETE")}>
            {provider.environment_key_set ? "Use environment key" : "Remove saved key"}
          </button> : null}
        </div>
      </fieldset>
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
    </form>
  );
}

export default function CoachProviderKeys({ providers }: { providers: CoachProviderStatus[] }) {
  return (
    <div style={{ marginTop: 24 }}>
      <h3 className="label">Provider API keys</h3>
      <p className="muted">These keys serve every account. Replacements are encrypted on the server and take effect on new requests without a restart. A saved key overrides the server environment key.</p>
      <div className="form-grid">
        {providers.map(provider => <ProviderKey key={provider.id} initial={provider} />)}
      </div>
    </div>
  );
}
