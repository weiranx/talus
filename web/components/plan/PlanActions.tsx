"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

// Accept or decline a drafted plan, or ask the engine for a fresh one from today.
// `acceptOnly`: setup's review step, where changing a step replaces declining.
export default function PlanActions({ draftId, replan, reason, acceptOnly }: {
  draftId?: number; replan?: boolean; reason?: string; acceptOnly?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function go(path: string, body?: object) {
    setBusy(path);
    setError(null);
    const res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}) }).catch(() => null);
    setBusy(null);
    if (!res?.ok) {
      const b = await res?.json().catch(() => ({}));
      return setError(typeof b?.detail === "string" ? b.detail : "Could not save. Is the engine running?");
    }
    router.refresh();
  }

  return (
    <div className="actions">
      {draftId ? (
        <>
          <button className="btn" disabled={!!busy} aria-busy={busy === `/api/plan/${draftId}/accept`} onClick={() => go(`/api/plan/${draftId}/accept`)}>{busy === `/api/plan/${draftId}/accept` ? "Accepting" : "Accept plan"}</button>
          {acceptOnly ? null : <button className="btn light outline" disabled={!!busy} aria-busy={busy === `/api/plan/${draftId}/decline`} onClick={() => go(`/api/plan/${draftId}/decline`)}>Keep my current plan</button>}
        </>
      ) : null}
      {replan ? (
        <button className={draftId ? "btn light outline" : "btn"} disabled={!!busy} aria-busy={busy === "/api/plan/draft"}
          onClick={() => go("/api/plan/draft", { reason: reason ?? "request" })}>
          {busy === "/api/plan/draft" ? "Building" : "Build a new plan from today"}
        </button>
      ) : null}
      {error ? <span className="error-text">{error}</span> : null}
    </div>
  );
}
