import type { ZoneMinutes } from "@/lib/types";
import { pct } from "@/lib/format";

export default function ZoneBar({ share, target = 0.8 }: { share: ZoneMinutes; target?: number }) {
  const total = share.z1 + share.z2 + share.z3;
  return (
    <div>
      <div style={{ position: "relative", height: 22 }}>
        <div className="zones" style={{ marginTop: 4 }} role="img"
          aria-label={`Zone 1 ${pct(share.z1)}, Zone 2 ${pct(share.z2)}, Zone 3 ${pct(share.z3)}`}>
          {total === 0 ? <div style={{ flex: 1, background: "var(--line)" }} /> : null}
          {(["z1", "z2", "z3"] as const).map((z) =>
            share[z] > 0 ? <div key={z} style={{ width: `${share[z] * 100}%`, background: `var(--${z})` }} /> : null,
          )}
        </div>
        <div style={{ position: "absolute", left: `${target * 100}%`, top: 0, width: 2, height: 22, background: "var(--target)" }} />
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 14px", fontSize: 13, marginTop: 8 }}>
        {(["z1", "z2", "z3"] as const).map((z, i) => (
          <div key={z} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="swatch" style={{ background: `var(--${z})` }} />
            Zone {i + 1} <span className="mono" style={{ fontWeight: 500 }}>{pct(share[z])}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
