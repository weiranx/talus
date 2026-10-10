import Pill from "@/components/Pill";
import ScoreTrend from "@/components/ScoreTrend";
import type { ReadinessScore as Score } from "@/lib/types";

const TONE = { go: "good", modify: "warn", rest: "bad" } as const;
const lower = (name: string) => (name === "HRV" ? name : name === "Resting HR" ? "resting HR" : name.toLowerCase());
const list = (names: string[]) => (names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`);

/** The engine's 0 to 100 readiness score: what cost points, what it could not see, and 28 days of it. */
export default function ReadinessScore({ s }: { s: Score }) {
  const lost = s.items.filter((i) => i.points < 0).sort((a, b) => a.points - b.points);
  const fine = s.items.filter((i) => i.used && i.points === 0);
  const missing = s.items.filter((i) => !i.used);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div className="mono" style={{ fontSize: 44, fontWeight: 600, lineHeight: 1 }} aria-label={`Readiness score ${s.score} of 100`}>
          {s.score}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
          <Pill tone={TONE[s.call]}>{s.label}</Pill>
          <div className="muted">of 100</div>
        </div>
      </div>
      <div className="muted">{s.basis}.</div>
      {lost.length ? (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          {lost.map((i) => (
            <li key={i.key} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{i.name}</div>
                <div className="muted">{i.text}</div>
              </div>
              <span className="mono" style={{ whiteSpace: "nowrap" }}>{i.points}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div>Nothing reads worse than your normal.</div>
      )}
      {fine.length && lost.length ? <div className="muted">No points off for {list(fine.map((i) => lower(i.name)))}.</div> : null}
      {s.held ? <div className="muted">{s.held}.</div> : null}
      {missing.length ? <div className="muted">Not used, no data: {list(missing.map((i) => lower(i.name)))}.</div> : null}
      <div>
        <div className="muted" style={{ fontSize: 12 }}>Last {s.trend.length} days</div>
        <ScoreTrend data={s.trend} />
      </div>
    </div>
  );
}
