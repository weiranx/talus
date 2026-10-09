"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import Pill from "@/components/Pill";
import { zoneColors } from "@/lib/format";
import type { ZoneSettings } from "@/lib/types";

type N = 3 | 5;

function rangeText(floors: number[], k: number, n: number): string {
  if (k === 0) return `under ${floors[0]} bpm`;
  if (k === n - 1) return "and up";
  return `to ${floors[k] - 1} bpm`;
}

function rising(floors: number[]): boolean {
  return floors.every((f, i) => Number.isFinite(f) && f >= 40 && f <= 240 && (i === 0 || f > floors[i - 1]));
}

export default function ZonesCard({ zones }: { zones: ZoneSettings }) {
  const router = useRouter();
  const startFor = (n: N) => zones.options[n].custom ?? zones.options[n].derived;
  const [n, setN] = useState<N>(zones.zones);
  const [draft, setDraft] = useState<string[]>(startFor(zones.zones).map(String));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const floors = draft.map(Number);
  const derived = zones.options[n].derived;
  const isDerived = floors.every((f, i) => f === derived[i]);
  const saved = n === zones.zones && floors.every((f, i) => f === zones.floors[i]);
  const ok = rising(floors);
  const colors = zoneColors(n);

  function pick(next: N) {
    setN(next);
    setDraft(startFor(next).map(String));
    setMessage(null);
  }

  async function save(reset = false) {
    setSaving(true);
    setMessage(null);
    const res = await fetch("/api/settings/zones", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(reset ? { zones: n, reset: true } : { zones: n, floors }),
    }).catch(() => null);
    setSaving(false);
    if (!res) return setMessage({ ok: false, text: "Could not save. Is the engine running?" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return setMessage({ ok: false, text: typeof body.detail === "string" ? body.detail : "Check the zone starts." });
    if (reset) setDraft(derived.map(String));
    setMessage({ ok: true, text: `Saved ${n} zones.${body.rescored ? ` Rescored ${body.rescored} activities to fill in time in zone.` : ""}` });
    router.refresh();
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Heart rate zones</h2>
        <Pill tone="neutral">{zones.custom ? `${zones.zones} zones, edited` : `${zones.zones} zones, from thresholds`}</Pill>
      </div>

      <div className="seg-tabs" role="radiogroup" aria-label="Number of zones" style={{ alignSelf: "flex-start" }}>
        {([3, 5] as const).map((k) => (
          <a key={k} href="#" role="radio" aria-checked={n === k} aria-current={n === k ? "true" : undefined}
            onClick={(e) => { e.preventDefault(); pick(k); }}>{k} zones</a>
        ))}
      </div>

      <div className="zone-list">
        {zones.options[n].names.map((name, k) => (
          <div key={k} className="zone-row">
            <span className="swatch" style={{ background: colors[k] }} />
            <span style={{ flex: "1 1 auto", minWidth: 0 }}>Zone {k + 1}, {name.toLowerCase()}</span>
            {k === 0 ? null : (
              <label className="zone-start">
                <span className="visually-hidden">Zone {k + 1} starts at, bpm</span>
                <input inputMode="numeric" value={draft[k - 1]} aria-invalid={!ok}
                  onChange={(e) => {
                    const next = [...draft];
                    next[k - 1] = e.target.value.replace(/\D/g, "");
                    setDraft(next);
                    setMessage(null);
                  }} />
              </label>
            )}
            <span className="mono zone-range">{ok ? rangeText(floors, k, n) : ""}</span>
          </div>
        ))}
      </div>
      {!ok ? <div className="error-text">Each zone has to start higher than the one before.</div> : null}
      <div className="muted">
        {n === 5
          ? "From your thresholds: zone 2 starts at 90% of AeT, zone 3 at AeT, zone 4 halfway to AnT, zone 5 just above AnT."
          : "From your thresholds: zone 2 starts at AeT and zone 3 just above AnT."}
        {" "}Zones set time in zone on Today, Load and Activity, and what the coach reads. Loads and the 80% target, which counts time below AeT, stay the same.
      </div>

      <div className="actions">
        <button className="btn" onClick={() => save()} disabled={!ok || saving || saved}>{saving ? "Saving" : "Save zones"}</button>
        {!isDerived ? (
          <button className="btn light outline" onClick={() => save(true)} disabled={saving}>Reset to thresholds</button>
        ) : null}
      </div>
      {message ? <div className={message.ok ? "ok-text" : "error-text"} role="status">{message.text}</div> : null}
    </section>
  );
}
