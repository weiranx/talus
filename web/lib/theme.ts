"use client";

import { useSyncExternalStore } from "react";

// Light, dark, or follow the system. Saved per browser; layout.tsx applies it before the first paint.
export type ThemeChoice = "light" | "dark" | "system";
export const THEME_KEY = "talus-theme";

export function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function saveChoice(choice: ThemeChoice) {
  try {
    if (choice === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Private windows can refuse storage; the choice still applies until reload.
  }
  if (choice === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
}

function resolved(): "light" | "dark" {
  const set = document.documentElement.dataset.theme;
  if (set === "light" || set === "dark") return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function subscribe(onChange: () => void) {
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const mo = new MutationObserver(onChange);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  mq.addEventListener("change", onChange);
  return () => {
    mo.disconnect();
    mq.removeEventListener("change", onChange);
  };
}

// The theme actually showing, for code that needs real colors (the map) rather than CSS variables.
export function useResolvedTheme(): "light" | "dark" {
  return useSyncExternalStore(subscribe, resolved, () => "light");
}

// "var(--z1)" to the color it currently stands for; other colors pass through.
export function cssColor(c: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(c);
  if (!m) return c;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || c;
}

// Runs in <head> before paint, so a saved choice never flashes the other theme.
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
