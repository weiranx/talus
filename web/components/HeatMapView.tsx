"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { dayLabel, mi, n0 } from "@/lib/format";
import { cssColor, useResolvedTheme } from "@/lib/theme";
import type { HeatActivity } from "@/lib/types";

// Quiet grey basemaps, so the routes are the brightest thing on the map. Free OpenFreeMap tiles, no key.
const STYLE = {
  light: process.env.NEXT_PUBLIC_HEAT_STYLE ?? "https://tiles.openfreemap.org/styles/positron",
  dark: process.env.NEXT_PUBLIC_MAP_STYLE_DARK ?? "https://tiles.openfreemap.org/styles/dark",
};
const blank = (bg: string) => ({ version: 8 as const, sources: {}, layers: [{ id: "bg", type: "background" as const, paint: { "background-color": bg } }] });

export type HeatTrack = HeatActivity & { coords: [number, number][] };
type Bounds = [[number, number], [number, number]];

function boundsOf(tracks: HeatTrack[]): Bounds | null {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const t of tracks) for (const [lo, la] of t.coords) {
    if (lo < w) w = lo; if (lo > e) e = lo; if (la < s) s = la; if (la > n) n = la;
  }
  return Number.isFinite(w) ? [[w, s], [e, n]] : null;
}

// Where most routes start: the busiest half-degree cell and its neighbours, so one trip abroad
// doesn't zoom the map out to a whole continent.
function homeOf(tracks: HeatTrack[]): HeatTrack[] {
  const cell = (t: HeatTrack) => t.coords[0].map((v) => Math.floor(v * 2)) as [number, number];
  const counts = new Map<string, number>();
  for (const t of tracks) { const k = cell(t).join(); counts.set(k, (counts.get(k) ?? 0) + 1); }
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  if (!top) return tracks;
  const [cx, cy] = top.split(",").map(Number);
  return tracks.filter((t) => { const [x, y] = cell(t); return Math.abs(x - cx) <= 1 && Math.abs(y - cy) <= 1; });
}

const geojson = (tracks: HeatTrack[]) => ({
  type: "FeatureCollection" as const,
  features: tracks.map((t) => ({
    type: "Feature" as const,
    properties: { id: t.id },
    geometry: { type: "LineString" as const, coordinates: t.coords },
  })),
});

export default function HeatMapView({ tracks }: { tracks: HeatTrack[] }) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useResolvedTheme();
  const map = useRef<import("maplibre-gl").Map | null>(null);
  const ready = useRef(false);
  const latest = useRef(tracks);
  latest.current = tracks;
  const [picked, setPicked] = useState<{ ids: number[] } | null>(null);
  const home = useMemo(() => boundsOf(homeOf(tracks)), [tracks]);
  const all = useMemo(() => boundsOf(tracks), [tracks]);
  const byId = useMemo(() => new Map(tracks.map((t) => [t.id, t])), [tracks]);

  useEffect(() => {
    let cancelled = false;
    import("maplibre-gl").then(({ default: maplibre }) => {
      if (cancelled || !el.current) return;
      const m = new maplibre.Map({ container: el.current, style: STYLE[theme], attributionControl: { compact: true } });
      map.current = m;
      m.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
      const start = boundsOf(homeOf(latest.current));
      if (start) m.fitBounds(start, { padding: 32, duration: 0, maxZoom: 14 });
      let fellBack = false;
      m.on("error", () => {
        if (!ready.current && !fellBack) {
          fellBack = true;
          m.setStyle(blank(theme === "dark" ? "#18201d" : "#eef1ef"));
        }
      });
      const heat = cssColor("var(--heat)");
      const draw = () => {
        if (ready.current) return;
        m.addSource("routes", { type: "geojson", data: geojson(latest.current) });
        m.addSource("picked", { type: "geojson", data: geojson([]) });
        // Each route is faint; where many overlap, they add up and the trail glows.
        m.addLayer({ id: "glow", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": heat, "line-blur": 3, "line-opacity": theme === "dark" ? 0.1 : 0.08,
            "line-width": ["interpolate", ["linear"], ["zoom"], 6, 3, 14, 10] } });
        m.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": heat, "line-opacity": theme === "dark" ? 0.45 : 0.4,
            "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.8, 14, 2.4] } });
        m.addLayer({ id: "picked", type: "line", source: "picked", layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": theme === "dark" ? "#ffffff" : "#15211d", "line-width": 3 } });
        ready.current = true;
        m.resize();
      };
      m.on("load", draw);
      m.on("style.load", () => fellBack && draw());
      m.on("click", (e) => {
        const box: [[number, number], [number, number]] = [[e.point.x - 5, e.point.y - 5], [e.point.x + 5, e.point.y + 5]];
        const ids = [...new Set(m.queryRenderedFeatures(box, { layers: ["routes"] }).map((f) => f.properties?.id as number))];
        setPicked(ids.length ? { ids } : null);
      });
      m.on("mousemove", "routes", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "routes", () => (m.getCanvas().style.cursor = ""));
    });
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
      ready.current = false;
    };
  }, [theme]);

  useEffect(() => {
    if (!ready.current || !map.current) return;
    (map.current.getSource("routes") as import("maplibre-gl").GeoJSONSource | undefined)?.setData(geojson(tracks));
    // New lines drawn while you look keep your pick; a filter that hides it clears it.
    const ids = new Set(tracks.map((t) => t.id));
    setPicked((p) => (p && p.ids.every((id) => ids.has(id)) ? p : null));
  }, [tracks]);

  useEffect(() => {
    if (!ready.current || !map.current) return;
    const sel = picked ? picked.ids.map((id) => byId.get(id)).filter((t): t is HeatTrack => !!t) : [];
    (map.current.getSource("picked") as import("maplibre-gl").GeoJSONSource | undefined)?.setData(geojson(sel));
  }, [picked, byId]);

  const fit = (b: Bounds | null) => b && map.current?.fitBounds(b, { padding: 32, maxZoom: 14 });
  const list = picked ? picked.ids.map((id) => byId.get(id)).filter((t): t is HeatTrack => !!t)
    .sort((a, b) => b.day.localeCompare(a.day)) : [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div className="map heat" role="img" aria-label="Every route in the selected range, brighter where you go often">
        <div ref={el} style={{ position: "absolute", inset: 0 }} />
        {list.length ? (
          <div className="heat-picked">
            <div className="label">{list.length === 1 ? "1 activity here" : `${n0(list.length)} activities here`}</div>
            {list.slice(0, 6).map((t) => (
              <Link key={t.id} href={`/activities/${t.id}`}>
                <span className="act-name">{t.name ?? "Activity"}</span>
                <span className="muted">{dayLabel(t.day, { month: "short", day: "numeric", year: "numeric" })} · {mi(t.distance_mi)} mi · {n0(t.gain_ft)} ft up</span>
              </Link>
            ))}
            {list.length > 6 ? <span className="muted">and {n0(list.length - 6)} more</span> : null}
          </div>
        ) : null}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
        <span className="muted">Zoom to</span>
        <div className="seg-tabs" role="group" aria-label="Zoom to">
          <button type="button" onClick={() => fit(home)}>Home area</button>
          <button type="button" onClick={() => fit(all)}>Everything</button>
        </div>
        <span className="muted">Click a line to see which runs went there.</span>
      </div>
    </div>
  );
}
