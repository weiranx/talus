"use client";

import { useEffect, useState } from "react";
import { readChoice, saveChoice, type ThemeChoice } from "@/lib/theme";

const CHOICES: [ThemeChoice, string][] = [["light", "Light"], ["dark", "Dark"], ["system", "System"]];

// Saved in this browser only, so a phone and a laptop can differ.
export default function AppearanceCard() {
  const [choice, setChoice] = useState<ThemeChoice | null>(null);
  useEffect(() => setChoice(readChoice()), []);

  function pick(c: ThemeChoice) {
    saveChoice(c);
    setChoice(c);
  }

  return (
    <section className="card">
      <h2 className="label" id="appearance-label">Appearance</h2>
      <div className="theme-choice" role="group" aria-labelledby="appearance-label">
        {CHOICES.map(([c, text]) => (
          <button key={c} type="button" aria-pressed={choice === c} onClick={() => pick(c)}>{text}</button>
        ))}
      </div>
      <div className="muted">System follows your device&apos;s light or dark setting. Saved in this browser.</div>
    </section>
  );
}
