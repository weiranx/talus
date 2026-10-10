"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Spinner } from "@/components/Loading";
import type { Explanation } from "@/lib/types";

// Tap a number to see what went into it: the inputs, the formula and the arithmetic, all from
// the engine. `path` is the engine's explain route, e.g. "ledger/descent/fatigue".
export default function Explain({ path, children }: { path: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Explanation | null>(null);
  const [error, setError] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);

  // Keep the panel on screen: slide it left when the number sits near the right edge.
  useLayoutEffect(() => {
    if (!open || !pop.current || !ref.current) return;
    const anchor = ref.current.getBoundingClientRect().left;
    const width = pop.current.offsetWidth;
    setShift(Math.min(0, window.innerWidth - 16 - (anchor + width)));
  }, [open, data, error]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !data) {
      const res = await fetch(`/api/explain/${path}`).catch(() => null);
      if (res?.ok) {
        setData(await res.json());
        setError(false);
      } else setError(true);
    }
  }

  return (
    <span ref={ref} className="explain">
      <button type="button" className="explain-num" aria-expanded={open} onClick={toggle} title="How is this worked out?">
        {children}
      </button>
      {open ? (
        <span ref={pop} className="explain-pop" style={{ left: shift }} role="dialog" aria-label={data?.title ?? "How this number is worked out"}>
          {error ? <span>Could not load the explanation.</span> : !data ? <span className="busy-line" role="status"><Spinner /> Loading…</span> : (
            <>
              <strong>{data.title}</strong>
              <span>{data.formula}</span>
              {data.inputs.length ? (
                <span className="explain-inputs">
                  {data.inputs.map((i) => (
                    <span key={i.label} style={{ display: "contents" }}>
                      <span className="muted">{i.label}</span>
                      <span className="mono">{i.value}</span>
                    </span>
                  ))}
                </span>
              ) : null}
              {data.steps.map((s) => <span key={s} className="mono explain-step">{s}</span>)}
              {data.note ? <span className="muted">{data.note}</span> : null}
              {data.unit ? <span className="muted">{data.unit}</span> : null}
            </>
          )}
        </span>
      ) : null}
    </span>
  );
}
