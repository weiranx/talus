"use client";

import { useState } from "react";

// Thumbs up or down on a coach reply, kept in the coach log next to the prompt and the data.
export default function CoachFeedback({ logId, initial, dark }: { logId: number; initial: number | null; dark?: boolean }) {
  const [rating, setRating] = useState<number | null>(initial);
  const [error, setError] = useState(false);

  async function rate(v: number) {
    const next = rating === v ? null : v;
    const res = await fetch(`/api/coach/feedback/${logId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ rating: next }),
    }).catch(() => null);
    setError(!res?.ok);
    if (res?.ok) setRating(next);
  }

  const style = (v: number) => ({
    minHeight: 32, padding: "0 12px", borderRadius: 999, fontSize: 13, cursor: "pointer",
    border: `1px solid ${dark ? "var(--feature-line)" : "var(--line)"}`,
    background: rating === v ? (dark ? "var(--on-feature)" : "var(--strong)") : "transparent",
    color: rating === v ? (dark ? "var(--feature)" : "var(--on-strong)") : dark ? "var(--feature-soft)" : "var(--ink)",
  });

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, color: dark ? "var(--feature-muted)" : "var(--muted)" }}>
      <span>Useful?</span>
      <button type="button" aria-pressed={rating === 1} style={style(1)} onClick={() => rate(1)}>Yes</button>
      <button type="button" aria-pressed={rating === -1} style={style(-1)} onClick={() => rate(-1)}>No</button>
      {error ? <span>Could not save.</span> : null}
    </div>
  );
}
