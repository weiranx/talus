"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

type Values = { soreness: number; energy: number; pain: number };

const QUESTIONS: { key: keyof Values; label: string; low: string; high: string }[] = [
  { key: "soreness", label: "Leg soreness", low: "none", high: "very sore" },
  { key: "energy", label: "Energy", low: "flat", high: "great" },
  { key: "pain", label: "Any pain", low: "none", high: "sharp" },
];

export default function CheckIn({ initial }: { initial: Values | null }) {
  const router = useRouter();
  const [values, setValues] = useState<Partial<Values>>(initial ?? {});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(initial == null);
  const complete = QUESTIONS.every((q) => values[q.key] != null);

  if (!editing && initial) {
    return (
      <section className="card">
        <h2 className="label">Morning check-in</h2>
        <div>Soreness {initial.soreness}, energy {initial.energy}, pain {initial.pain} (of 5).</div>
        <button className="btn light" style={{ border: "1px solid var(--line)", alignSelf: "flex-start" }} onClick={() => setEditing(true)}>
          Change
        </button>
      </section>
    );
  }

  async function save() {
    setSaving(true);
    setError(null);
    const res = await fetch("/api/checkin", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(values),
    }).catch(() => null);
    setSaving(false);
    if (!res?.ok) {
      setError("Could not save. Is the engine running?");
      return;
    }
    setEditing(false);
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">Morning check-in, about 10 seconds</h2>
      {QUESTIONS.map((q) => (
        <fieldset key={q.key} style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          <legend style={{ fontWeight: 600, padding: 0, marginBottom: 6 }}>
            {q.label} <span className="muted">1 {q.low}, 5 {q.high}</span>
          </legend>
          <div className="scale">
            {[1, 2, 3, 4, 5].map((v) => (
              <button key={v} type="button" aria-pressed={values[q.key] === v}
                onClick={() => setValues({ ...values, [q.key]: v })}>
                {v}
              </button>
            ))}
          </div>
        </fieldset>
      ))}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button className="btn" disabled={!complete || saving} onClick={save}>
          {saving ? "Saving" : "Save check-in"}
        </button>
        {error ? <span style={{ color: "var(--bad-ink)", fontSize: 13 }}>{error}</span> : null}
      </div>
    </section>
  );
}
