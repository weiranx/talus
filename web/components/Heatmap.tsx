"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Skel, Spinner } from "@/components/Loading";
import { dayLabel, duration, mi, n0 } from "@/lib/format";
import { decodePolyline } from "@/lib/polyline";
import type { HeatActivity, HeatKind, HeatmapData } from "@/lib/types";
import type { HeatTrack } from "@/components/HeatMapView";

const HeatMapView = dynamic(() => import("@/components/HeatMapView"), {
  ssr: false,
  loading: () => <Skel h="min(68vh, 620px)" r={12} />,
});

const RANGES = { "30": "30 days", "90": "90 days", "365": "1 year", all: "All" } as const;
type Range = keyof typeof RANGES;
const KINDS: Record<HeatKind, string> = { trail: "Trail run", run: "Run", hike: "Hike", ride: "Ride", other: "Other" };
const MEASURES = { effort: "Effort mi", miles: "Miles", gain: "Feet up" } as const;
type Measure = keyof typeof MEASURES;

const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const addDays = (iso: string, n: number) => {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDay(new Date(y, m - 1, d + n));
};
const mondayOf = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return addDays(iso, -((new Date(y, m - 1, d).getDay() + 6) % 7));
};

export default function Heatmap() {
  const [data, setData] = useState<HeatmapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<Range>("all");
  const [kinds, setKinds] = useState<Set<HeatKind>>(new Set());
  const [view, setView] = useState<"map" | "calendar">("map");

  // Lines the engine hasn't drawn yet are read in the background; ask again until they are all there.
  useEffect(() => {
    let stop = false, timer: ReturnType<typeof setTimeout>;
    const load = () =>
      fetch("/api/heatmap", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Engine returned ${r.status}`))))
        .then((d: HeatmapData) => {
          if (stop) return;
          setData(d);
          if (d.pending > 0) timer = setTimeout(load, 3000);
        })
        .catch((e) => !stop && setError(e instanceof Error ? e.message : "Could not reach the engine."));
    load();
    return () => { stop = true; clearTimeout(timer); };
  }, []);

  const today = isoDay(new Date());
  const since = range === "all" ? null : addDays(today, -Number(range) + 1);
  const present = useMemo(
    () => (Object.keys(KINDS) as HeatKind[]).filter((k) => data?.activities.some((a) => a.kind === k)),
    [data],
  );
  const shown = useMemo(
    () => (data?.activities ?? []).filter((a) => (!since || a.day >= since) && (kinds.size === 0 || kinds.has(a.kind))),
    [data, since, kinds],
  );
  const tracks = useMemo<HeatTrack[]>(
    () => shown.filter((a) => a.line).map((a) => ({ ...a, coords: decodePolyline(a.line!) })),
    [shown],
  );

  if (error) return <section className="card"><div className="muted">Could not load your routes: {error}</div></section>;
  if (!data) return <HeatmapSkeleton />;
  if (data.activities.length === 0) return <section className="card"><div className="muted">No scored activities yet.</div></section>;

  const totals = shown.reduce(
    (t, a) => ({ n: t.n + 1, mi: t.mi + a.distance_mi, up: t.up + (a.gain_ft ?? 0), down: t.down + (a.loss_ft ?? 0), s: t.s + a.moving_time_s }),
    { n: 0, mi: 0, up: 0, down: 0, s: 0 },
  );
  const toggle = (k: HeatKind) => setKinds((prev) => {
    const next = new Set(prev);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    return next;
  });
  const noGps = shown.length - tracks.length - shown.filter((a) => a.pending).length;

  return (
    <>
      <section className="card">
        <div className="heat-controls">
          <div className="seg-tabs" role="group" aria-label="Time range">
            {(Object.keys(RANGES) as Range[]).map((r) => (
              <button key={r} type="button" aria-pressed={r === range} onClick={() => setRange(r)}>{RANGES[r]}</button>
            ))}
          </div>
          {present.length > 1 ? (
            <div className="seg-tabs" role="group" aria-label="Sport">
              <button type="button" aria-pressed={kinds.size === 0} onClick={() => setKinds(new Set())}>All</button>
              {present.map((k) => (
                <button key={k} type="button" aria-pressed={kinds.has(k)} onClick={() => toggle(k)}>{KINDS[k]}</button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="stat-grid">
          <div className="stat"><div className="label">Activities</div><div className="v">{n0(totals.n)}</div></div>
          <div className="stat"><div className="label">Distance</div><div className="v">{n0(totals.mi)}<span className="unit">mi</span></div></div>
          <div className="stat"><div className="label">Climb</div><div className="v">{n0(totals.up)}<span className="unit">ft up</span></div></div>
          <div className="stat"><div className="label">Descent</div><div className="v">{n0(totals.down)}<span className="unit">ft down</span></div></div>
          <div className="stat"><div className="label">Moving time</div><div className="v">{n0(totals.s / 3600)}<span className="unit">h</span></div></div>
        </div>
      </section>

      <section className="card">
        <div className="heat-controls">
          <div className="seg-tabs" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "map"} onClick={() => setView("map")}>Routes</button>
            <button type="button" aria-pressed={view === "calendar"} onClick={() => setView("calendar")}>Calendar</button>
          </div>
          <span className="muted" style={{ fontSize: 13 }}>
            {data.pending ? <><Spinner /> Drawing {n0(data.pending)} more route{data.pending === 1 ? "" : "s"}… </> : null}
            {view === "map" && noGps > 0 ? `${n0(noGps)} without GPS not on the map.` : null}
          </span>
        </div>
        {view === "map" ? (
          tracks.length ? <HeatMapView tracks={tracks} /> : <div className="muted">No routes in this range.</div>
        ) : (
          <Calendar acts={shown} from={since ?? shown.at(-1)?.day ?? today} today={today} />
        )}
      </section>
    </>
  );
}

function Calendar({ acts, from, today }: { acts: HeatActivity[]; from: string; today: string }) {
  const [measure, setMeasure] = useState<Measure>("effort");
  const [picked, setPicked] = useState<string | null>(null);

  const byDay = useMemo(() => {
    const m = new Map<string, HeatActivity[]>();
    for (const a of acts) m.set(a.day, [...(m.get(a.day) ?? []), a]);
    return m;
  }, [acts]);
  const value = (list: HeatActivity[] | undefined) =>
    (list ?? []).reduce((s, a) => s + (measure === "miles" ? a.distance_mi : measure === "gain" ? a.gain_ft ?? 0 : a.effort_mi), 0);
  // Four shades, split at the quartiles of the days with something on them, so one huge day doesn't wash out the rest.
  const cuts = useMemo(() => {
    const v = [...byDay.values()].map(value).filter((x) => x > 0).sort((a, b) => a - b);
    return [0.25, 0.5, 0.75].map((q) => v[Math.floor(q * (v.length - 1))] ?? 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [byDay, measure]);
  const level = (v: number) => (v <= 0 ? 0 : v <= cuts[0] ? 1 : v <= cuts[1] ? 2 : v <= cuts[2] ? 3 : 4);

  const weeks: string[] = [];
  for (let w = mondayOf(from); w <= today; w = addDays(w, 7)) weeks.push(w);
  const scroller = useRef<HTMLDivElement>(null);
  // Newest weeks in view when the calendar opens or its range changes, not after every click.
  useEffect(() => {
    if (scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth;
  }, [from]);
  const pickedActs = picked ? byDay.get(picked) ?? [] : [];

  return (
    <>
      <div className="heat-controls">
        <div className="seg-tabs" role="group" aria-label="Shade days by">
          {(Object.keys(MEASURES) as Measure[]).map((m) => (
            <button key={m} type="button" aria-pressed={m === measure} onClick={() => setMeasure(m)}>{MEASURES[m]}</button>
          ))}
        </div>
        <span className="heat-legend">
          Less
          {[0, 1, 2, 3, 4].map((l) => (
            <span key={l} className="d" style={{ background: l === 0 ? "var(--line)" : l === 4 ? "var(--heat)" : `color-mix(in srgb, var(--heat) ${[0, 28, 50, 75][l]}%, var(--line))` }} />
          ))}
          More
        </span>
      </div>
      <div className="heat-cal" ref={scroller}>
        <div className="heat-cal-grid" role="grid" aria-label="Activity by day">
          {weeks.map((w) => {
            const days = Array.from({ length: 7 }, (_, i) => addDays(w, i));
            const first = days.find((d) => d.endsWith("-01"));
            const label = first ?? (w === weeks[0] ? w : null);
            return [
              <span key={`${w}m`} className="m">{label ? dayLabel(label, { month: "short" }) : ""}</span>,
              ...days.map((d) => {
                if (d < from || d > today) return <span key={d} className="d out" />;
                const list = byDay.get(d);
                const tip = `${dayLabel(d, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}: ` + (list
                  ? `${mi(list.reduce((s, a) => s + a.distance_mi, 0))} mi, ${n0(list.reduce((s, a) => s + (a.gain_ft ?? 0), 0))} ft up, ${n0(list.reduce((s, a) => s + (a.loss_ft ?? 0), 0))} ft down`
                  : "rest");
                return (
                  <span key={d} className={`d${picked === d ? " sel" : ""}`} data-l={level(value(list))} title={tip} role="gridcell"
                    aria-label={tip} onClick={() => setPicked(list ? (picked === d ? null : d) : null)} style={list ? { cursor: "pointer" } : undefined} />
                );
              }),
            ];
          })}
        </div>
      </div>
      {pickedActs.length ? (
        <div className="table-wrap">
          <table className="linked">
            <tbody>
              {pickedActs.map((a) => (
                <tr key={a.id}>
                  <td>{dayLabel(a.day, { weekday: "short", month: "short", day: "numeric" })}</td>
                  <td><Link href={`/activities/${a.id}`}>{a.name ?? "Activity"}</Link></td>
                  <td className="num">{mi(a.distance_mi)} mi</td>
                  <td className="num">{n0(a.gain_ft)} ft up</td>
                  <td className="num">{n0(a.loss_ft)} ft down</td>
                  <td className="num">{duration(a.moving_time_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 13 }}>
          Effort mi adds a mile for every 528 ft of climbing, so distance and vert count together. Click a day to see its activities.
        </div>
      )}
    </>
  );
}

export function HeatmapSkeleton() {
  return (
    <>
      <section className="card"><Skel h={38} w={360} r={10} /><Skel h={56} /></section>
      <section className="card"><Skel h={38} w={200} r={10} /><Skel h="min(68vh, 620px)" r={12} /></section>
    </>
  );
}
