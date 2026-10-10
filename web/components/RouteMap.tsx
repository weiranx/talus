"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { cssColor, useResolvedTheme } from "@/lib/theme";

// Free OpenFreeMap tiles, no key. Swap with NEXT_PUBLIC_MAP_STYLE(_DARK) once a terrain provider is chosen.
const STYLE = {
  light: process.env.NEXT_PUBLIC_MAP_STYLE ?? "https://tiles.openfreemap.org/styles/liberty",
  dark: process.env.NEXT_PUBLIC_MAP_STYLE_DARK ?? "https://tiles.openfreemap.org/styles/dark",
};
// Used when the tile style cannot load, so the track still draws.
const blank = (bg: string) => ({ version: 8 as const, sources: {}, layers: [{ id: "bg", type: "background" as const, paint: { "background-color": bg } }] });
// The white edge around the track becomes a dark one on the dark map.
const CASING = { light: "#ffffff", dark: "#0f1513" };

const GRADE_STOPS: [number, string][] = [[-20, "#6e3c9e"], [-8, "#b89bd6"], [0, "#d9dfdc"], [8, "#f0b25a"], [20, "#c96a00"]];

export function gradeColor(g: number): string {
  for (let i = GRADE_STOPS.length - 1; i >= 0; i--) if (g >= GRADE_STOPS[i][0]) return GRADE_STOPS[i][1];
  return GRADE_STOPS[0][1];
}

type Props = {
  lat: number[];
  lon: number[];
  colors: string[];
  highlight: [number, number] | null; // point index range
};

export default function RouteMap({ lat, lon, colors, highlight }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useResolvedTheme();
  const map = useRef<import("maplibre-gl").Map | null>(null);
  const ready = useRef(false);
  // The map loads asynchronously; draw() reads the latest props, not the first render's.
  const latest = useRef({ colors, highlight });
  latest.current = { colors, highlight };

  const features = () => ({
    type: "FeatureCollection" as const,
    features: lat.slice(1).map((_, i) => ({
      type: "Feature" as const,
      properties: { color: cssColor(latest.current.colors[i + 1]) },
      geometry: { type: "LineString" as const, coordinates: [[lon[i], lat[i]], [lon[i + 1], lat[i + 1]]] },
    })),
  });
  const highlighted = () => {
    const hl = latest.current.highlight;
    return {
      type: "Feature" as const,
      properties: {},
      geometry: {
        type: "LineString" as const,
        coordinates: hl ? lat.slice(hl[0], hl[1] + 1).map((la, i) => [lon[hl[0] + i], la]) : [],
      },
    };
  };

  useEffect(() => {
    let cancelled = false;
    import("maplibre-gl").then(({ default: maplibre }) => {
      if (cancelled || !el.current) return;
      const m = new maplibre.Map({ container: el.current, style: STYLE[theme], attributionControl: { compact: true } });
      map.current = m;
      m.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
      const lons = lon.filter(Number.isFinite), lats = lat.filter(Number.isFinite);
      m.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 32, duration: 0 });
      let fellBack = false;
      m.on("error", () => {
        if (!ready.current && !fellBack) {
          fellBack = true;
          m.setStyle(blank(theme === "dark" ? "#18201d" : "#eef1ef"));
        }
      });
      const draw = () => {
        if (ready.current) return;
        m.addSource("track", { type: "geojson", data: features() });
        m.addSource("hl", { type: "geojson", data: highlighted() });
        m.addLayer({ id: "track-casing", type: "line", source: "track", paint: { "line-color": CASING[theme], "line-width": 6 }, layout: { "line-cap": "round", "line-join": "round" } });
        m.addLayer({ id: "track", type: "line", source: "track", paint: { "line-color": ["get", "color"], "line-width": 3.5 }, layout: { "line-cap": "round", "line-join": "round" } });
        m.addLayer({ id: "hl", type: "line", source: "hl", paint: { "line-color": "#f0b25a", "line-width": 9, "line-opacity": 0.6 }, layout: { "line-cap": "round" } }, "track-casing");
        ready.current = true;
        m.resize();
      };
      m.on("load", draw);
      m.on("style.load", () => fellBack && draw());
    });
    return () => {
      cancelled = true;
      map.current?.remove();
      ready.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lon, theme]);

  useEffect(() => {
    if (!ready.current || !map.current) return;
    (map.current.getSource("track") as import("maplibre-gl").GeoJSONSource | undefined)?.setData(features());
    (map.current.getSource("hl") as import("maplibre-gl").GeoJSONSource | undefined)?.setData(highlighted());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colors, highlight]);

  return <div ref={el} className="map" role="img" aria-label="Route map, track colored by the selected measure" />;
}
