import Pill from "@/components/Pill";
import { mi, n0, pct } from "@/lib/format";
import { cover, FOCUS_TEXT, VOL_DIMS } from "@/lib/plan";
import type { WeekProgress } from "@/lib/types";

const STATE = { covered: { tone: "good", text: "On track" }, close: { tone: "warn", text: "A little short" },
  short: { tone: "bad", text: "Short" }, over: { tone: "warn", text: "Over target" } } as const;

// This week against its target: done (solid) and still scheduled (light), for miles and both
// directions of vert, plus the share of time below AeT.
export default function ThisWeek({ w }: { w: WeekProgress }) {
  const c = cover(w);
  if (!w.target || !c) return null;
  const fmt = (key: string, v: number) => (key === "miles" ? `${mi(v)} mi` : `${n0(v)} ft`);
  const easy = w.done?.below_aet_pct;
  return (
    <section className="card" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <h2 className="label">This week, {FOCUS_TEXT[w.target.focus].toLowerCase()}</h2>
        <Pill tone={STATE[c.state].tone}>{STATE[c.state].text}</Pill>
      </div>
      {VOL_DIMS.map(({ key, label }) => {
        const target = w.target![key], done = w.done?.[key] ?? 0, ahead = w.ahead[key];
        const scale = Math.max(target, done + ahead, 1);
        return (
          <div key={key} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 14 }}>
              <span>{label}</span>
              <span className="mono" style={{ fontSize: 13 }}>
                {fmt(key, done)} done{ahead > 0 ? ` + ${fmt(key, ahead)} scheduled` : ""} of {fmt(key, target)}
              </span>
            </div>
            <div style={{ position: "relative", height: 10, borderRadius: 5, background: "var(--bg)", overflow: "hidden" }}
              role="img" aria-label={`${label}: ${fmt(key, done)} done and ${fmt(key, ahead)} scheduled of ${fmt(key, target)}`}>
              <div style={{ position: "absolute", inset: 0, width: `${(done / scale) * 100}%`, background: "var(--ink)" }} />
              <div style={{ position: "absolute", top: 0, bottom: 0, left: `${(done / scale) * 100}%`, width: `${(ahead / scale) * 100}%`, background: "var(--ink)", opacity: 0.3 }} />
              <div style={{ position: "absolute", top: 0, bottom: 0, left: `calc(${(target / scale) * 100}% - 1px)`, width: 2, background: "var(--warn-ink)" }} />
            </div>
          </div>
        );
      })}
      <div className="muted">
        {easy == null ? "No heart-rate data this week yet." : `${pct(easy)} of this week's time below AeT, against the 80% goal.`}
        {c.state === "short" ? " Add a run on the calendar, or let the week come in under target." : ""}
        {c.state === "over" ? " Your runs this week go well past the target. Load shows whether that ramps too fast." : ""}
      </div>
    </section>
  );
}
