"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { CoachAction } from "@/lib/types";

const DONE_LABEL: Record<string, string> = { done: "Done", dismissed: "Skipped", failed: "Didn't work", running: "Working" };

// Something the coach offered to do. Nothing runs until Confirm; the result stays on the card.
export default function ActionCard({ initial }: { initial: CoachAction }) {
  const [a, setA] = useState(initial);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  async function act(verb: "confirm" | "dismiss") {
    setBusy(true);
    const res = await fetch(`/api/coach/actions/${a.id}/${verb}`, { method: "POST" }).catch(() => null);
    setBusy(false);
    if (!res?.ok) return setA({ ...a, result: "Could not reach the engine. Try again." });
    const out = await res.json();
    setA({ ...a, status: out.status, result: out.result });
    if (verb === "confirm") router.refresh(); // the calendar, goals or loads may have changed
  }

  const open = a.status === "proposed";
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "8px 10px", background: "var(--surface)",
      display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
      <div style={{ color: "var(--ink)" }}>{a.summary}</div>
      {open ? (
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" style={{ minHeight: 36, padding: "0 14px", fontSize: 14 }} disabled={busy} aria-busy={busy}
            onClick={() => act("confirm")}>Confirm</button>
          <button className="btn light outline" style={{ minHeight: 36, padding: "0 14px", fontSize: 14 }} disabled={busy}
            onClick={() => act("dismiss")}>Not now</button>
        </div>
      ) : (
        <div className={a.status === "failed" ? "error-text" : "muted"} style={{ fontSize: 12 }}>
          {DONE_LABEL[a.status] ?? a.status}{a.result && a.status !== "dismissed" ? `: ${a.result}` : ""}
        </div>
      )}
      {open && a.result ? <div className="error-text" style={{ fontSize: 12 }}>{a.result}</div> : null}
    </div>
  );
}
