"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Settings } from "@/lib/types";

const SOURCE = {
  saved: "Saved here.",
  env: "From TALUS_SLEEP_NEED_H in the engine's .env until you save one here.",
  default: "The 8 h default until you save your own.",
} as const;

export default function ReadinessSettings({ readiness }: { readiness: Settings["readiness"] }) {
  const router = useRouter();
  const [hours, setHours] = useState(String(readiness.sleep_need_h));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const value = Number(hours);
  const valid = hours !== "" && value >= 4 && value <= 12;

  async function save() {
    setSaving(true);
    setMessage(null);
    const res = await fetch("/api/settings/readiness", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ sleep_need_h: value }),
    }).catch(() => null);
    setSaving(false);
    if (!res) return setMessage({ ok: false, text: "Could not save. Is the engine running?" });
    if (!res.ok) return setMessage({ ok: false, text: `Could not save (${res.status}).` });
    setHours(String(Math.round(value * 4) / 4)); // the engine keeps quarter hours
    setMessage({ ok: true, text: "Saved. Today reads your sleep against this from now on." });
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">Readiness</h2>
      <label className="field">
        <span className="field-label">Sleep you need, hours a night</span>
        <input type="number" inputMode="decimal" min={4} max={12} step={0.25} value={hours}
          onChange={(e) => setHours(e.target.value)} style={{ maxWidth: 120 }} />
        <span className="muted">
          {SOURCE[readiness.sleep_need_source]} Sleep reads short when last night is 1.5 h under this, or the 7-day average is 1 h under.
        </span>
      </label>
      <div className="actions">
        <button className="btn" onClick={save} disabled={!valid || value === readiness.sleep_need_h || saving} aria-busy={saving}>
          {saving ? "Saving" : "Save sleep need"}
        </button>
      </div>
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
    </section>
  );
}
