"use client";

import { useMemo, useState } from "react";
import ElevationProfile from "@/components/ElevationProfile";
import RouteMap, { gradeColor } from "@/components/RouteMap";
import { duration, mi, n0, zoneColors, zoneKeys } from "@/lib/format";
import type { ActivityDetail } from "@/lib/types";

type Mode = "zone" | "grade";

function grades(distance: number[], altitude: number[]): number[] {
  // Grade in percent over roughly 0.05 mi either side of each point.
  return distance.map((d, i) => {
    let a = i, b = i;
    while (a > 0 && d - distance[a] < 0.05) a--;
    while (b < distance.length - 1 && distance[b] - d < 0.05) b++;
    const run = (distance[b] - distance[a]) * 5280;
    return run > 0 ? ((altitude[b] - altitude[a]) / run) * 100 : 0;
  });
}

export default function ActivityView({ a }: { a: ActivityDetail }) {
  const s = a.streams;
  const hasZone = s.zone != null;
  const n = a.zone_model.zones;
  const zColors = zoneColors(n);
  const zMinutes = a.hr_zone_minutes ?? {};
  const [mode, setMode] = useState<Mode>(hasZone ? "zone" : "grade");
  const [selected, setSelected] = useState<number | null>(null);

  const colors = useMemo(() => {
    if (mode === "zone" && s.zone) return s.zone.map((z) => zColors[z - 1]);
    if (s.altitude_ft) return grades(s.distance_mi, s.altitude_ft).map(gradeColor);
    return s.distance_mi.map(() => zColors[1]);
  }, [mode, s, zColors]);

  const highlight = useMemo<[number, number] | null>(() => {
    if (selected == null) return null;
    const seg = a.segments[selected];
    const from = s.time_s.findIndex((t) => t >= seg.start_s);
    let to = s.time_s.length - 1;
    while (to > 0 && s.time_s[to] > seg.end_s) to--;
    return from < 0 || to <= from ? null : [from, to];
  }, [selected, a.segments, s.time_s]);

  const counts: Record<string, number> = {};
  const segName = (kind: string) => {
    counts[kind] = (counts[kind] ?? 0) + 1;
    return `${kind[0].toUpperCase()}${kind.slice(1)} ${counts[kind]}`;
  };

  return (
    <>
      <section className="card">
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <h2 className="label">Route</h2>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="muted">Color by</span>
            <div className="seg-tabs" role="group" aria-label="Color by">
              {(["zone", "grade"] as const).map((m) => (
                <a key={m} href="#" role="button" aria-current={mode === m ? "true" : undefined}
                  aria-disabled={m === "zone" && !hasZone}
                  onClick={(e) => { e.preventDefault(); if (m === "grade" || hasZone) setMode(m); }}>
                  {m === "zone" ? "Zone" : "Grade"}
                </a>
              ))}
            </div>
          </div>
        </div>
        {s.lat && s.lon ? <RouteMap lat={s.lat} lon={s.lon} colors={colors} highlight={highlight} /> : <div className="muted">No GPS track in this file.</div>}
        {s.altitude_ft ? (
          <ElevationProfile distance={s.distance_mi} altitude={s.altitude_ft} heartRate={s.heart_rate} colors={colors} highlight={highlight} />
        ) : null}
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 13 }}>
          {mode === "zone"
            ? zoneKeys(n).map((z, i) => (
                <div key={z} style={{ display: "flex", alignItems: "center", gap: 6 }}
                  title={`${a.zone_model.list[i].name}, ${a.zone_model.list[i].text}`}>
                  <span className="swatch" style={{ background: zColors[i] }} />
                  Zone {i + 1}, {duration((zMinutes[z] ?? 0) * 60)}
                </div>
              ))
            : [["Steep down", "#6e3c9e"], ["Down", "#b89bd6"], ["Flat", "#d9dfdc"], ["Up", "#f0b25a"], ["Steep up", "#c96a00"]].map(([l, c]) => (
                <div key={l} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="swatch" style={{ background: c }} />{l}
                </div>
              ))}
        </div>
      </section>

      <section className="card">
        <h2 className="label">Terrain segments</h2>
        <div className="table-wrap">
          <table style={{ minWidth: 640 }}>
            <thead>
              <tr>
                <th scope="col">Segment</th>
                <th scope="col" className="num">Miles</th>
                <th scope="col" className="num">Vert, ft</th>
                <th scope="col" className="num">Grade</th>
                <th scope="col">Mode</th>
                <th scope="col" className="num">Time</th>
                <th scope="col">Zones</th>
                <th scope="col" className="num">Load</th>
              </tr>
            </thead>
            <tbody>
              {a.segments.map((seg) => {
                const vert = seg.kind === "climb" ? `+${n0(seg.gain_ft)}` : seg.kind === "descent" ? `−${n0(seg.loss_ft)}` : `+${n0(seg.gain_ft)} / −${n0(seg.loss_ft)}`;
                const zm = seg.hr_zone_minutes;
                const zones = zm ? zoneKeys(n).filter((z) => zm[z] >= 1).map((z) => `Z${z.slice(1)} ${Math.round(zm[z])}`).join(", ") : "–";
                const modeText = seg.hike_share > 0.8 ? "Hike" : seg.hike_share > 0.2 ? "Run and hike" : "Run";
                return (
                  <tr key={seg.index} className={selected === seg.index ? "hl" : undefined}>
                    <td>
                      <button type="button" onClick={() => setSelected(selected === seg.index ? null : seg.index)}
                        aria-pressed={selected === seg.index}
                        style={{ background: "none", border: 0, padding: 0, color: "var(--link)", font: "inherit", cursor: "pointer", minHeight: 32 }}>
                        {segName(seg.kind)}
                      </button>
                    </td>
                    <td className="num">{mi(seg.distance_mi)}</td>
                    <td className="num">{vert}</td>
                    <td className="num">{Math.round(Math.abs(seg.avg_grade))}%</td>
                    <td>{modeText}</td>
                    <td className="num">{duration(seg.time_s)}</td>
                    <td>{zones}</td>
                    <td className="num">{seg.kind === "climb" ? n0(seg.climb_load) : seg.kind === "descent" ? n0(seg.descent_load) : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="muted">Pick a segment to find it on the map and profile. Load is climb load on climbs and descent load on descents; steep feet count more than gentle ones.</div>
      </section>
    </>
  );
}
