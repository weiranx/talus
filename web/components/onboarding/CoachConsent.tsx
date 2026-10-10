"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Onboarding } from "@/lib/types";
import { mark } from "./steps";

const SENT = [
  "Daily load numbers: cardio, climb, descent and distance, and how they compare with your normal",
  "Each activity's summary: miles, feet up and down, time, and minutes in each heart rate zone",
  "Your heart rate thresholds, readiness, check-in answers, and nightly sleep, resting heart rate and HRV",
  "Your plan's weeks and your objective's distance, vert and date, and what you type into chat",
];
const NEVER = [
  "GPS tracks, start locations or maps",
  "Raw heart rate streams, second by second",
  "Your name, email, activity titles or the name of your objective",
];

// The spec's consent step: what goes to the model and what never does, then on or off.
export default function CoachConsent({ ob }: { ob: Onboarding }) {
  const router = useRouter();
  const [style, setStyle] = useState(ob.coach.style);
  const [busy, setBusy] = useState<"on" | "off" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(on: boolean) {
    setBusy(on ? "on" : "off");
    setError(null);
    const ok = await mark("done", "coach", { coach_enabled: on, coach_style: style });
    if (!ok) {
      setBusy(null);
      return setError("Could not save. Is the engine running?");
    }
    router.push("/welcome?step=tour");
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">The AI coach</h2>
      <div>
        The coach writes the reason behind today&apos;s call, a read on each activity, and your plan&apos;s explanation, using
        Claude from Anthropic. Every number it uses comes from Talus, and Talus checks its numbers before you see them. With
        it off, every screen still works with plain rule-based text.
      </div>
      <div className="row" style={{ gap: 16 }}>
        <div style={{ flex: "1 1 260px" }}>
          <div style={{ fontWeight: 600 }}>Sent to the model</div>
          <ul className="method-list">{SENT.map((s) => <li key={s}>{s}</li>)}</ul>
        </div>
        <div style={{ flex: "1 1 260px" }}>
          <div style={{ fontWeight: 600 }}>Never sent</div>
          <ul className="method-list">{NEVER.map((s) => <li key={s}>{s}</li>)}</ul>
        </div>
      </div>
      <div className="muted">Anthropic doesn&apos;t train models on API traffic. You can turn the coach off any time in Settings.</div>
      <label className="field" style={{ maxWidth: 360 }}>
        <span className="field-label">Coaching style</span>
        <select value={style} onChange={(e) => setStyle(e.target.value)}>
          {ob.coach.styles.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        <span className="muted">{ob.coach.styles.find((o) => o.key === style)?.description}</span>
      </label>
      {!ob.coach.key_set ? <div className="muted">This Talus server has no Anthropic key yet, so the coach stays on rule-based text until it does.</div> : null}
      <div className="actions">
        <button className="btn" onClick={() => choose(true)} disabled={busy !== null} aria-busy={busy === "on"}>Turn on the coach</button>
        <button className="btn light outline" onClick={() => choose(false)} disabled={busy !== null} aria-busy={busy === "off"}>Load tracking only</button>
      </div>
      {error ? <div className="error-text" role="status">{error}</div> : null}
    </section>
  );
}
