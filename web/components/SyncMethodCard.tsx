"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Settings } from "@/lib/types";

const METHODS: { key: Settings["sync_method"]; title: string; detail: string }[] = [
  { key: "intervals", title: "intervals.icu", detail: "Talus pulls new activities and overnight data from intervals.icu." },
  { key: "watch", title: "Garmin watch", detail: "Sync activities straight from your watch over Bluetooth in the iPhone app, or in Chrome on Windows or Android." },
];

export default function SyncMethodCard({ method }: { method: Settings["sync_method"] }) {
  const router = useRouter();
  const [value, setValue] = useState(method);
  const [error, setError] = useState<string | null>(null);

  async function choose(next: Settings["sync_method"]) {
    const before = value;
    setValue(next);
    setError(null);
    const res = await fetch("/api/settings/sync-method", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ method: next }),
    }).catch(() => null);
    if (!res?.ok) {
      setValue(before);
      return setError(res ? `Could not save (${res.status}).` : "Could not reach the engine. Is it running?");
    }
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">Sync method</h2>
      <div role="radiogroup" aria-label="Sync method" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {METHODS.map((m) => (
          <label key={m.key} style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
            <input type="radio" name="sync-method" checked={value === m.key} onChange={() => choose(m.key)} style={{ marginTop: 4 }} />
            <span>
              <strong>{m.title}</strong>
              <span className="muted" style={{ display: "block" }}>{m.detail}</span>
            </span>
          </label>
        ))}
      </div>
      {error ? <div className="error-text">{error}</div> : null}
      <div className="muted">File upload works with either.</div>
    </section>
  );
}
