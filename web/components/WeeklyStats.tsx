import { dayLabel, duration, mi, n0 } from "@/lib/format";
import type { LoadData, WeekTotals } from "@/lib/types";

type Key = keyof Omit<WeekTotals, "start">;

// The columns, in the order they read. Distance and vert lead and carry a bar for comparing weeks
// at a glance; the rest are hidden on phones.
const COLS: { key: Key; label: string; fmt: (v: number) => string; bar?: string; extra?: boolean }[] = [
  { key: "distance_mi", label: "Miles", fmt: mi, bar: "var(--distance)" },
  { key: "gain_ft", label: "Ft up", fmt: n0, bar: "var(--climb)" },
  { key: "loss_ft", label: "Ft down", fmt: n0, bar: "var(--descent)" },
  { key: "moving_time_s", label: "Time", fmt: (v) => duration(v) },
  { key: "activities", label: "Activities", fmt: n0, extra: true },
  { key: "effort_mi", label: "Effort mi", fmt: mi, extra: true },
  { key: "cardio_load", label: "Cardio", fmt: n0, extra: true },
  { key: "climb_load", label: "Climb", fmt: n0, extra: true },
  { key: "descent_load", label: "Descent", fmt: n0, extra: true },
];

/** Change from `prev` to `cur` as a signed percent, or null when there is nothing to compare. */
function change(cur: number, prev: number): string | null {
  if (!prev) return cur ? "new" : null;
  const p = Math.round(((cur - prev) / prev) * 100);
  return p === 0 ? "same" : `${p > 0 ? "↑" : "↓"} ${Math.abs(p)}%`;
}

function Delta({ cur, prev, title }: { cur: number; prev: number; title: string }) {
  const c = change(cur, prev);
  return c ? <div className="wk-delta" title={title}>{c}</div> : null;
}

export default function WeeklyStats({ weekly, lastToDate }: { weekly: LoadData["weekly"]; lastToDate: WeekTotals }) {
  if (!weekly.length) return null;
  const now = weekly[weekly.length - 1];
  const last = weekly[weekly.length - 2];
  const full = weekly.slice(0, -1).filter((w) => w.activities > 0);
  const avg = Object.fromEntries(COLS.map((c) => [c.key, full.length ? full.reduce((s, w) => s + w[c.key], 0) / full.length : 0])) as Record<Key, number>;
  const max = Object.fromEntries(COLS.map((c) => [c.key, Math.max(...weekly.map((w) => w[c.key]), 1)])) as Record<Key, number>;
  const byDay = dayLabel(addDays(now.start, now.days - 1), { weekday: "short" });
  const rows = [...weekly].reverse();

  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ fontSize: 17, fontWeight: 600 }}>Weekly stats</h2>
        <div className="muted">Monday to Sunday. Each change compares with the week before.</div>
      </div>

      {last ? (
        <div className="stat-grid wk-tiles">
          {COLS.slice(0, 4).map((c) => (
            <div key={c.key} className="stat">
              <div className="label">{c.label}, last week</div>
              <div className="v">{c.fmt(last[c.key])}</div>
              {weekly.length > 2 ? (
                <div className="muted">{change(last[c.key], weekly[weekly.length - 3][c.key]) ?? "–"} vs prior week</div>
              ) : null}
              {full.length > 1 ? <div className="muted">{change(last[c.key], avg[c.key]) ?? "–"} vs your average</div> : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className="table-wrap">
        <table className="wk-table">
          <thead>
            <tr>
              <th scope="col">Week</th>
              {COLS.map((c) => <th key={c.key} scope="col" className={`num${c.extra ? " wk-x" : ""}`}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((w, i) => {
              const current = w === now;
              const prev = current ? lastToDate : rows[i + 1];
              const title = current ? `vs last week through ${byDay}` : "vs the week before";
              return (
                <tr key={w.start}>
                  <th scope="row">
                    {current ? "This week" : dayLabel(w.start, { month: "short", day: "numeric" })}
                    {current && w.days < 7 ? <div className="muted">so far, {w.days} of 7 days</div> : null}
                  </th>
                  {COLS.map((c) => (
                    <td key={c.key} className={`num${c.extra ? " wk-x" : ""}`}>
                      <div className="wk-v">{c.fmt(w[c.key])}</div>
                      {c.bar ? <div className="wk-bar" style={{ ["--w" as string]: w[c.key] / max[c.key], ["--c" as string]: c.bar }} /> : null}
                      {prev ? <Delta cur={w[c.key]} prev={prev[c.key]} title={title} /> : null}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
          {full.length > 1 ? (
            <tfoot>
              <tr>
                <th scope="row">Average<div className="muted">{full.length} full weeks</div></th>
                {COLS.map((c) => <td key={c.key} className={`num${c.extra ? " wk-x" : ""}`}><div className="wk-v">{c.fmt(avg[c.key])}</div></td>)}
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
      <div className="muted">Counts every activity, the same as the Activities page. Bars are scaled to the biggest of these weeks.</div>
    </section>
  );
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
