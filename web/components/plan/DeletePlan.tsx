"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

// Delete the objective and its plan, after a confirm step. Your own scheduled runs and your
// training history stay.
export default function DeletePlan({ name }: { name: string }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    const res = await fetch("/api/plan/objective", { method: "DELETE" }).catch(() => null);
    setBusy(false);
    if (!res?.ok) return setError("Could not delete. Is the engine running?");
    setConfirming(false);
    router.refresh();
  }

  if (!confirming) {
    return <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }}
      onClick={() => setConfirming(true)}>Delete plan</button>;
  }
  return (
    <div role="alertdialog" aria-label="Delete the plan" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span style={{ fontSize: 14 }}>Delete {name} and its plan? Your own scheduled runs stay.</span>
      <button type="button" className="btn danger" style={{ minHeight: 36, padding: "0 12px" }} disabled={busy}
        aria-busy={busy} onClick={remove}>{busy ? "Deleting" : "Delete"}</button>
      <button type="button" className="btn light outline" style={{ minHeight: 36, padding: "0 12px" }} disabled={busy}
        onClick={() => setConfirming(false)}>Cancel</button>
      {error ? <span className="error-text">{error}</span> : null}
    </div>
  );
}
