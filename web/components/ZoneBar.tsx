import { pct, zoneColors, zoneKeys } from "@/lib/format";
import type { HrZoneMinutes, ZoneModel } from "@/lib/types";

// Time in each zone of the chosen model, with the 80% line and the share below AeT it is judged on.
export default function ZoneBar({ share, model, belowAet, target = 0.8 }:
  { share: HrZoneMinutes; model: ZoneModel | null; belowAet: number; target?: number }) {
  const n = model?.zones ?? 3;
  const keys = zoneKeys(n);
  const colors = zoneColors(n);
  const total = keys.reduce((t, z) => t + (share[z] ?? 0), 0);
  return (
    <div>
      <div style={{ position: "relative", height: 22 }}>
        <div className="zones" style={{ marginTop: 4 }} role="img"
          aria-label={keys.map((z, i) => `Zone ${i + 1} ${pct(share[z] ?? 0)}`).join(", ")}>
          {total === 0 ? <div style={{ flex: 1, background: "var(--line)" }} /> : null}
          {keys.map((z, i) =>
            share[z] > 0 ? <div key={z} style={{ width: `${share[z] * 100}%`, background: colors[i] }} /> : null,
          )}
        </div>
        <div style={{ position: "absolute", left: `${target * 100}%`, top: 0, width: 2, height: 22, background: "var(--target)" }} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 13, marginTop: 8 }}>
        {keys.map((z, i) => (
          <div key={z} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="swatch" style={{ background: colors[i] }} />
            Zone {i + 1} <span className="mono" style={{ fontWeight: 500 }}>{pct(share[z] ?? 0)}</span>
          </div>
        ))}
      </div>
      <div className="muted" style={{ marginTop: 6 }}>
        Below AeT <span className="mono" style={{ color: "var(--ink)" }}>{pct(belowAet)}</span>, target {pct(target)}.
      </div>
    </div>
  );
}
