import Pill from "@/components/Pill";
import { mi, n0 } from "@/lib/format";
import { hours } from "@/lib/plan";
import type { Prediction as P } from "@/lib/types";

const CONFIDENCE: Record<string, { tone: "good" | "warn" | "neutral"; text: string }> = {
  good: { tone: "good", text: "Solid" }, fair: { tone: "warn", text: "Fair" }, rough: { tone: "neutral", text: "Rough" },
};

const SOURCE: Record<string, string> = { flat_cost: "From your flat pace", climb_rate: "From your climbing rate" };

function pace(min: number): string {
  const s = Math.round(min * 60);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// The objective's finish estimate: the route priced grade by grade at your own pace, with splits
// at its aid stations (or every few miles) and the paces it used.
export default function Prediction({ p }: { p: P }) {
  if (!p.ready || p.mid_h == null) {
    return (
      <section className="card" style={{ gap: 8 }}>
        <h2 className="label">Finish estimate</h2>
        <div className="muted" style={{ fontSize: 14 }}>
          Needs {p.needs} runs with elevation in the last {p.days} days to price the route at your own pace on each grade; Talus has {p.runs}.
          {p.pending ? ` ${p.pending} more ${p.pending === 1 ? "is" : "are"} still being read.` : ""}
        </div>
      </section>
    );
  }
  const c = CONFIDENCE[p.confidence ?? "fair"];
  const splits = p.splits ?? [];
  const named = splits.some((s) => s.name && s.name !== "Finish");
  return (
    <section className="card" style={{ gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Finish estimate</h2>
        <Pill tone={c.tone}>{c.text}</Pill>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <span className="mono" style={{ fontSize: 32, fontWeight: 500 }}>{hours(p.mid_h)}</span>
        <span className="muted mono" style={{ fontSize: 14 }}>{hours(p.low_h!)} to {hours(p.high_h!)} moving</span>
      </div>
      <div className="muted" style={{ fontSize: 13 }}>
        Your pace on each grade from {p.runs} runs in the last {p.days} days, {p.from === "profile" ? "along the route's profile" : "over the route's totals"},
        slowing {Math.round((p.fade?.per_h ?? 0.02) * 100)}% for every hour past {p.fade?.from_h ?? 3}. The range is your slower and faster runs on the same grades.
        Stops at aid stations aren&apos;t in it.
      </div>
      {p.notes?.length ? <ul className="muted" style={{ fontSize: 13, margin: 0, paddingLeft: 18 }}>{p.notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}

      {splits.length ? (
        <div className="table-wrap">
          <table style={{ minWidth: 0, fontSize: 14 }}>
            <thead>
              <tr>
                <th scope="col">{named ? "To" : "Mile"}</th>
                <th scope="col" className="num">mi</th>
                <th scope="col" className="num">ft up</th>
                <th scope="col" className="num">ft down</th>
                <th scope="col" className="num">Split</th>
                <th scope="col" className="num">Clock</th>
              </tr>
            </thead>
            <tbody>
              {splits.map((s) => (
                <tr key={s.to_mi} title={`Clock ${hours(s.low_clock_min / 60)} to ${hours(s.high_clock_min / 60)}`}>
                  <td style={{ whiteSpace: "normal" }}>
                    {s.name ?? `${mi(s.to_mi)}`}
                    {named ? <div className="muted mono" style={{ fontSize: 12 }}>mi {mi(s.to_mi)}</div> : null}
                  </td>
                  <td className="num">{mi(s.distance_mi)}</td>
                  <td className="num">{n0(s.gain_ft)}</td>
                  <td className="num">{n0(s.loss_ft)}</td>
                  <td className="num">{hours(s.minutes / 60)}</td>
                  <td className="num">{hours(s.clock_min / 60)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {p.bands?.length ? (
        <details>
          <summary className="muted" style={{ cursor: "pointer", minHeight: 24, fontSize: 13 }}>Your pace on each grade of the route</summary>
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table style={{ minWidth: 0, fontSize: 14 }}>
              <thead>
                <tr>
                  <th scope="col">Grade</th>
                  <th scope="col" className="num">Route mi</th>
                  <th scope="col" className="num">min/mi</th>
                  <th scope="col" className="num">ft/h</th>
                  <th scope="col">From</th>
                </tr>
              </thead>
              <tbody>
                {p.bands.map((b) => (
                  <tr key={b.key}>
                    <td style={{ whiteSpace: "normal" }}>{b.label}</td>
                    <td className="num">{mi(b.distance_mi)}</td>
                    <td className="num">{pace(b.pace_min_mi)}</td>
                    <td className="num">{b.vert_ft_h ? n0(b.vert_ft_h) : "–"}</td>
                    <td className="muted" style={{ whiteSpace: "normal", fontSize: 13 }}>
                      {b.source === "yours" ? `${b.runs} runs${b.hike_share != null && b.hike_share >= 0.5 ? ", mostly hiked" : ""}` : SOURCE[b.source]}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  );
}
