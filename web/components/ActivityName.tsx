"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Dots } from "@/components/Loading";

type Props = { id: number; name: string | null; sourceName: string | null; by: "you" | "ai" | "source" };

const field: React.CSSProperties = {
  flex: "1 1 100%", minWidth: 0, minHeight: 44, border: "1px solid var(--line)", borderRadius: 10, padding: "0 12px",
  font: "inherit", fontSize: 20, fontWeight: 600, color: "var(--ink)", background: "var(--field)",
};

// The activity's title, with Rename. The coach can suggest a name; what you save always wins.
export default function ActivityName({ id, name, sourceName, by }: Props) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name ?? "");
  const [busy, setBusy] = useState<"save" | "suggest" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  async function save(next: string) {
    setBusy("save");
    setError(null);
    const res = await fetch(`/api/activities/${id}/name`, { method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: next }) }).catch(() => null);
    setBusy(null);
    if (!res?.ok) return setError("Could not save the name.");
    setEditing(false);
    router.refresh();
  }

  async function suggest() {
    setBusy("suggest");
    setError(null);
    const res = await fetch(`/api/activities/${id}/name/suggest`, { method: "POST" }).catch(() => null);
    setBusy(null);
    if (!res?.ok) return setError("Could not get a suggestion.");
    const out = await res.json();
    setValue(out.name);
    if (out.source !== "coach") setError("The AI coach isn't available, so this name comes from the place alone.");
  }

  if (!editing) {
    return (
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h1 className="title">{name ?? "Activity"}</h1>
        <button className="link-btn" onClick={() => { setValue(name ?? ""); setEditing(true); }}>Rename</button>
        {by === "ai" ? <span className="muted" style={{ fontSize: 12 }} title={sourceName ? `Device name: ${sourceName}` : undefined}>Named by the coach</span> : null}
      </div>
    );
  }
  return (
    <form onSubmit={(e) => { e.preventDefault(); save(value); }} style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 640 }}>
      <label className="visually-hidden" htmlFor="activity-name">Activity name</label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input id="activity-name" value={value} maxLength={60} onChange={(e) => setValue(e.target.value)} style={field} autoFocus />
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn" disabled={busy !== null || !value.trim()} aria-busy={busy === "save"}>Save</button>
        <button type="button" className="btn light outline" disabled={busy !== null} onClick={suggest} aria-busy={busy === "suggest"}>
          Suggest with AI
        </button>
        {by === "you" ? (
          <button type="button" className="link-btn" disabled={busy !== null} onClick={() => save("")}>Use the automatic name</button>
        ) : null}
        <button type="button" className="link-btn" disabled={busy !== null} onClick={() => setEditing(false)}>Cancel</button>
        {busy === "suggest" ? <span className="muted" style={{ display: "inline-flex", gap: 8, alignItems: "center", fontSize: 13 }}><Dots />Finding the place</span> : null}
      </div>
      {error ? <span className="muted" style={{ fontSize: 13 }}>{error}</span> : null}
    </form>
  );
}
