import Link from "next/link";
import { dayLabel, duration, mi, n0 } from "@/lib/format";
import type { Ledger, NextWeek as Data } from "@/lib/types";

// Distance and vert lead, as everywhere else; time stands in for the cardio ledger.
const COLS: { ledger: Ledger; key: "miles" | "gain_ft" | "loss_ft" | "hours"; label: string; noun: string;
  fmt: (v: number) => string; round: (v: number) => number }[] = [
  { ledger: "distance", key: "miles", label: "Miles", noun: "mi", fmt: mi, round: (v) => Math.round(v) },
  { ledger: "climb", key: "gain_ft", label: "Ft up", noun: "ft up", fmt: n0, round: (v) => Math.round(v / 100) * 100 },
  { ledger: "descent", key: "loss_ft", label: "Ft down", noun: "ft down", fmt: n0, round: (v) => Math.round(v / 100) * 100 },
  { ledger: "cardio", key: "hours", label: "Time", noun: "hours", fmt: (h) => duration(h * 3600), round: (v) => Math.round(v * 4) / 4 },
];

type Verdict = "eases" | "holds" | "builds" | "over";
const VERDICT: Record<Verdict, string> = { eases: "Eases off", holds: "Holds your normal", builds: "Builds", over: "Too fast" };

function verdict(v: number, hold: number, limit: number): Verdict {
  if (v > limit) return "over";
  if (v < hold * 0.9) return "eases";
  if (v <= hold * 1.1) return "holds";
  return "builds";
}

const list = (xs: string[]) => (xs.length < 3 ? xs.join(" and ") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

/** How much the next 7 days can hold, beside what's scheduled in them: the Load page's "what now". */
export default function NextWeek({ data }: { data: Data }) {
  const cols = COLS.filter((c) => data.room[c.ledger]);
  if (!cols.length) return null;
  const s = data.scheduled;
  const planned = s.runs > 0;
  const fmtRound = (c: (typeof COLS)[number], v: number) => c.fmt(c.round(v));
  const states = cols.map((c) => {
    const r = data.room[c.ledger]!;
    const v = s[c.key];
    return { c, r, v, verdict: v == null ? null : verdict(v, r.hold, r.limit) };
  });
  const over = states.filter((x) => x.verdict === "over");
  const vert = states.filter((x) => x.c.key !== "hours" && x.verdict);
  const dist = states.find((x) => x.c.key === "miles"), up = states.find((x) => x.c.key === "gain_ft");
  const pair = (k: "hold" | "limit") => list([dist, up].filter(Boolean).map((x) => `${fmtRound(x!.c, x!.r[k])} ${x!.c.noun}`));

  let tone: "warn" | "ok" = "ok";
  let head: string;
  if (!planned) {
    head = `Nothing scheduled yet. About ${pair("hold")} keeps your normal; past ${pair("limit")} you'd be ramping too fast.`;
  } else if (over.length) {
    tone = "warn";
    head = `The schedule runs past the ramp limit on ${list(over.map((x) => x.c.label.toLowerCase()))}. Shorten or move a run.`;
  } else if (vert.length && vert.every((x) => x.verdict === "eases")) {
    head = "The schedule is lighter than your normal: a good recovery week, and your normal will dip a little.";
  } else if (vert.some((x) => x.verdict === "builds")) {
    head = "The schedule builds on your normal and stays under the ramp limit.";
  } else {
    head = "The schedule holds your normal, with room to spare before ramping too fast.";
  }

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ fontSize: 17, fontWeight: 600 }}>Next 7 days</h2>
        <div className="muted">
          {dayLabel(data.start, { weekday: "short", month: "short", day: "numeric" })} to{" "}
          {dayLabel(data.end, { weekday: "short", month: "short", day: "numeric" })}
          {planned ? `, ${s.runs} run${s.runs === 1 ? "" : "s"} scheduled` : ""}
        </div>
      </div>
      <div className={tone === "warn" ? "nw-head warn" : "nw-head"}>
        {head} <Link href="/plan">{planned ? "Open the plan" : "Schedule runs"}</Link>
      </div>
      <div className="nw-grid">
        {states.map(({ c, r, v, verdict: vd }) => {
          const top = Math.max(r.limit, v ?? 0, r.hold) * 1.12 || 1;
          const at = (x: number) => `${Math.min(100, (x / top) * 100)}%`;
          return (
            <div key={c.key} className="nw-col" style={{ ["--c" as string]: `var(--${c.ledger})` }}>
              <div className="label">{c.label}{planned ? ", scheduled" : ""}</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <span className="nw-v">{planned && v != null ? c.fmt(v) : "–"}</span>
                {planned && vd ? <span className={`nw-tag ${vd}`}>{VERDICT[vd]}</span> : null}
              </div>
              <div className="nw-meter" role="img"
                aria-label={`${c.label}: ${planned && v != null ? c.fmt(v) + " scheduled, " : ""}${fmtRound(c, r.hold)} holds your normal, ${fmtRound(c, r.limit)} is the ramp limit`}>
                <div className="nw-over" style={{ left: at(r.limit) }} />
                {planned && v != null ? <div className="nw-fill" style={{ width: at(v) }} /> : null}
                <div className="nw-tick" style={{ left: at(r.hold) }} />
                <div className="nw-tick limit" style={{ left: at(r.limit) }} />
              </div>
              <div className="nw-scale">
                <span>Normal <span className="mono">{fmtRound(c, r.hold)}</span></span>
                <span>Limit <span className="mono">{fmtRound(c, r.limit)}</span></span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="muted" style={{ fontSize: 13 }}>
        Normal: the week that keeps your 6-week average where it is. Limit: the most before your last 7 days run 30% above
        your normal, spread evenly. One big day lands harder than the same total spread out.
      </div>
    </section>
  );
}
