"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { BusyLine } from "@/components/Loading";
import { dayLabel, mi, n0 } from "@/lib/format";
import type { RouteMatches as Matches } from "@/lib/types";

export default function RouteMatches({ initial, call }: { initial: Matches; call: string }) {
  const [data, setData] = useState(initial);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState(false);
  const [waiting, setWaiting] = useState(false);

  useEffect(() => {
    if (!initial.pending && refresh === 0) return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    async function poll() {
      setWaiting(true);
      try {
        const res = await fetch(`/api/routes/today?call=${call}`, { cache: "no-store", signal: abort.signal });
        if (!res.ok) throw new Error("Routes unavailable");
        const next: Matches = await res.json();
        if (abort.signal.aborted) return;
        setData(next);
        setError(false);
        if (next.pending && ++attempts < 30) timer = setTimeout(poll, 3000);
      } catch {
        if (!abort.signal.aborted) setError(true);
      } finally {
        if (!abort.signal.aborted) setWaiting(false);
      }
    }
    timer = setTimeout(poll, refresh ? 0 : 3000);
    return () => { abort.abort(); clearTimeout(timer); };
  }, [call, initial.pending, refresh]);

  return (
    <section className="card" aria-labelledby="routes-heading">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h2 className="label" id="routes-heading">Find a route for today</h2>
        <Link href="/activities">Your activities</Link>
      </div>
      {data.target ? <p style={{ margin: 0 }}>{data.target.text}</p> : null}
      <p className="muted" style={{ margin: 0 }}>{data.pending && !data.routes.length && data.status === "empty"
        ? "Reading your recorded GPS routes. Matches will appear here as they're ready."
        : data.message}</p>
      {data.routes.length ? (
        <ol style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 14 }}>
          {data.routes.map((r, i) => (
            <li key={r.activity_id} style={{ borderTop: "1px solid var(--line)", paddingTop: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
                <Link href={`/activities/${r.activity_id}`} style={{ fontWeight: 600 }}>{r.name}</Link>
                {i === 0 ? <span className="muted" style={{ fontSize: 12 }}>Best fit</span> : null}
              </div>
              <div className="mono" style={{ fontSize: 13, marginTop: 6 }}>
                {mi(r.distance_mi)} mi · {n0(r.gain_ft)} ft up · {n0(r.loss_ft)} ft down
              </div>
              <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                {r.time_basis === "easy_pace" ? `About ${r.minutes} min at your recent easy pace` : `${r.minutes} min last time`}
                {" · "}{dayLabel(r.last_done, { month: "short", day: "numeric", year: "numeric" })}
              </div>
              <p style={{ margin: "6px 0 0", fontSize: 14 }}>{r.reasons.join(" ")}</p>
            </li>
          ))}
        </ol>
      ) : null}
      {data.pending ? <BusyLine>More routes are being read from your history.</BusyLine> : null}
      {error ? <p className="muted" role="status" style={{ margin: 0 }}>Couldn&apos;t refresh routes. Try again.</p> : null}
      {data.pending || error ? (
        <div><button type="button" className="btn light outline" disabled={waiting}
          onClick={() => setRefresh((n) => n + 1)}>{waiting ? "Checking routes…" : "Refresh routes"}</button></div>
      ) : null}
      {data.status === "empty" && !data.pending ? <Link href="/settings">Import outdoor activities</Link> : null}
    </section>
  );
}
