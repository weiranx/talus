import Link from "next/link";
import EngineDown from "@/components/EngineDown";
import IntensityChart from "@/components/IntensityChart";
import LedgerChart from "@/components/LedgerChart";
import LinkPending from "@/components/LinkPending";
import NextWeek from "@/components/NextWeek";
import Pill from "@/components/Pill";
import WeeklyStats from "@/components/WeeklyStats";
import { engine, engineFailed } from "@/lib/api";
import { chartDay, dayLabel, duration, LEDGER_LABEL, LEDGER_UNIT, ledgerBadge, ledgerDecimals, ledgerNum, ledgerUnit, mi, n0, pct, zoneColors, zoneKeys, signedPct } from "@/lib/format";
import { LEDGERS, type LoadData } from "@/lib/types";

export const dynamic = "force-dynamic";

const RANGES = { "6w": { days: 42, label: "6 wk" }, "6m": { days: 182, label: "6 mo" }, "1y": { days: 365, label: "1 yr" } } as const;

export default async function LoadPage({ searchParams }: { searchParams: Promise<{ range?: string }> }) {
  const { range: r } = await searchParams;
  const range = (r && r in RANGES ? r : "6m") as keyof typeof RANGES;
  let data: LoadData;
  try {
    data = await engine<LoadData>(`/api/load?days=${RANGES[range].days}`);
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }
  const th = data.thresholds;

  return (
    <>
      <header className="page-head">
        <div style={{ flex: "1 1 320px" }}>
          <h1 className="title">Load</h1>
          <div className="muted">
            Solid: your normal, a 6-week average. Dashed: your last 7 days. Shaded: room before ramping too fast.
            {data.projection?.until ? " Dotted: where your scheduled runs take it." : ""}
          </div>
          {data.history.gap ? (
            <div className="muted">
              No activities from {dayLabel(data.history.gap.from, { month: "short", day: "numeric" })} to{" "}
              {dayLabel(data.history.gap.to, { month: "short", day: "numeric" })}, so that stretch is treated as missing
              data, not rest. Charts and your normal start again on{" "}
              {dayLabel(data.history.since!, { month: "short", day: "numeric" })}.
            </div>
          ) : null}
        </div>
        <nav className="seg-tabs" aria-label="Time range">
          {Object.entries(RANGES).map(([k, v]) => (
            <Link key={k} href={`/load?range=${k}`} aria-current={k === range ? "true" : undefined}>{v.label}<LinkPending /></Link>
          ))}
        </nav>
      </header>

      <NextWeek data={data.next_week} />

      <WeeklyStats weekly={data.weekly} lastToDate={data.last_week_to_date} />

      <div className="ledger-grid">
        {LEDGERS.map((name) => {
          const l = data.ledgers[name];
          if (!l.today) return null;
          const badge = ledgerBadge(l.today);
          const spike = l.series.slice(-7).find((d) => d.flags.some((f) => f.key === "spike"));
          const nd = ledgerDecimals(name), unit = ledgerUnit(name);
          return (
            <section key={name} className="card ledger-card" style={{ ["--accent" as string]: `var(--${name})` }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="swatch" style={{ background: `var(--${name})` }} />
                  <h2 style={{ fontSize: 17, fontWeight: 600 }}>{LEDGER_LABEL[name]}</h2>
                </div>
                <Pill tone={badge.tone}>{badge.text}</Pill>
              </div>
              <div>
                {l.today.vs_normal == null ? (
                  <div className="big">{l.today.vs_normal_text.replace(/^./, (c) => c.toUpperCase())}</div>
                ) : (
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
                    <span className="mono" style={{ fontSize: 30, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.1 }}>
                      {signedPct(l.today.vs_normal)}
                    </span>
                    <span className="muted">vs your normal</span>
                  </div>
                )}
                <div className="muted">
                  Last 7 days <span className="mono" style={{ color: "var(--ink)" }}>{ledgerNum(l.today.fatigue, nd) + unit}</span> a day. Your normal{" "}
                  <span className="mono" style={{ color: "var(--ink)" }}>{ledgerNum(l.today.fitness, nd) + unit}</span>.
                </div>
              </div>
              <LedgerChart series={l.series.map(chartDay)} projected={data.projection?.series[name] ?? []} color={`var(--${name})`} label={LEDGER_LABEL[name]} decimals={nd} unit={unit} height={160} />
              {(data.projection?.warnings ?? []).filter((w) => w.ledgers.includes(name)).map((w) => (
                <div key={w.week} style={{ color: "var(--warn-ink)", fontSize: 14 }}>
                  Week of {dayLabel(w.week, { month: "short", day: "numeric" })}: {w.text} <Link href="/plan">Change the schedule</Link>
                </div>
              ))}
              <div className="muted" style={{ fontSize: 13 }}>
                {spike ? `${dayLabel(spike.day, { weekday: "long" })} was your biggest ${name} day in 6 weeks. ` : ""}
                {LEDGER_UNIT[name]}
              </div>
            </section>
          );
        })}
      </div>

      <div className="row">
        <section className="card" style={{ flex: "1 1 380px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
            <h2 className="label">Intensity by week</h2>
            <div className="muted" style={{ fontSize: 12 }}>Red line: 80% below AeT target</div>
          </div>
          <IntensityChart weeks={data.intensity_by_week} model={data.zone_model} />
          <div style={{ display: "flex", gap: 14, fontSize: 13 }}>
            {zoneKeys(data.zone_model?.zones ?? 3).map((z, i) => (
              <div key={z} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span className="swatch" style={{ background: zoneColors(data.zone_model?.zones ?? 3)[i] }} />Zone {i + 1}
              </div>
            ))}
          </div>
        </section>

        <section className="card" style={{ flex: "1 1 380px" }}>
          <h2 className="label">Biggest days, last 8 weeks</h2>
          {data.biggest_days.length === 0 ? <div className="muted">No activities in the last 8 weeks.</div> : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Activity</th>
                    <th scope="col" className="num">Miles</th>
                    <th scope="col" className="num">Gain, ft</th>
                    <th scope="col" className="num">Effort mi</th>
                    <th scope="col" className="num">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {data.biggest_days.map((a) => (
                    <tr key={a.id}>
                      <td>{dayLabel(a.day, { month: "short", day: "numeric" })}</td>
                      <td><Link href={`/activities/${a.id}`}>{a.name ?? "Activity"}</Link></td>
                      <td className="num">{mi(a.distance_mi)}</td>
                      <td className="num">{n0(a.gain_ft)}</td>
                      <td className="num">{mi(a.effort_mi)}</td>
                      <td className="num">{duration(a.moving_time_s)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {th ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 24px", paddingTop: 8 }}>
              <div>AeT <span className="mono">{n0(th.aet)}</span> <span className="muted">{th.source.includes("provisional") ? "provisional" : th.source}</span></div>
              <div>AnT <span className="mono">{n0(th.ant)}</span></div>
              <div>Aerobic gap <span className="mono">{pct(th.aerobic_gap)}</span> <span className="muted">goal: under 10%</span></div>
            </div>
          ) : null}
        </section>
      </div>
    </>
  );
}
