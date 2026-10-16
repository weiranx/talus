import Link from "next/link";
import Explain from "@/components/Explain";
import Sparkline from "@/components/Sparkline";
import { LEDGER_LABEL, ledgerBadge, ledgerDecimals, ledgerNum, ledgerUnit, signedPct } from "@/lib/format";
import { loadComparison } from "@/lib/today";
import { LEDGERS, type Ledger, type Today } from "@/lib/types";

const SYSTEMS: Ledger[] = ["cardio", "climb", "descent"];

export default function TodayLoad({ today: t }: { today: Today }) {
  const { usual, widths } = loadComparison(SYSTEMS.map((name) => {
    const d = t.ledgers[name];
    return !d || d.building ? null : d.vs_normal;
  }));
  const limiter = t.readiness.legs.state !== "fresh" ? t.readiness.legs.limiter : null;
  return (
    <section className="today-load" aria-labelledby="today-load-title">
      <div className="today-section-head">
        <h2 id="today-load-title">How you&apos;re carrying the week</h2>
        <Link href="/load">View load <span aria-hidden>›</span></Link>
      </div>
      <p className="today-caption">Recent load compared with your usual level.</p>
      <div className="today-load-rows">
        {SYSTEMS.map((name, index) => {
          const d = t.ledgers[name];
          const width = widths[index];
          const unknown = !d || d.building || width == null;
          const text = d?.building ? "Building your normal" : d?.vs_normal_text ?? "No history";
          return (
            <div className={`today-load-row${limiter === name ? " is-limiter" : ""}`} key={name}>
              <span className="today-load-name">{LEDGER_LABEL[name]}</span>
              <div className={`today-comparison${unknown ? " is-unavailable" : ""}`} role="img"
                aria-label={`${LEDGER_LABEL[name]}: ${unknown ? text : `${signedPct(d.vs_normal!)} compared with your usual level`}`}>
                {unknown ? <span className="today-comparison-empty">{text}</span> : <>
                  <span className="today-comparison-track" />
                  <span className="today-comparison-fill" style={{ width: `${width}%` }} />
                  <span className="today-comparison-usual" style={{ left: `${usual}%` }}><span>Usual</span></span>
                </>}
              </div>
              <span className="today-load-value">
                {unknown ? <span className="today-caption">—</span> : <Explain path={`ledger/${name}/vs_normal`}>{signedPct(d.vs_normal!)}</Explain>}
              </span>
            </div>
          );
        })}
      </div>
      {limiter ? <p className="today-limiter-note">{t.readiness.legs.detail}</p> : null}
      <details className="today-disclosure">
        <summary>Load details &amp; distance</summary>
        <div className="today-load-details">
          {LEDGERS.map((name) => {
            const d = t.ledgers[name];
            if (!d) return null;
            const nd = ledgerDecimals(name), unit = ledgerUnit(name);
            return <div key={name}>
              <h3>{LEDGER_LABEL[name]} <span className="today-caption">{ledgerBadge(d).text}</span></h3>
              <p className="today-caption">Last 7 days <Explain path={`ledger/${name}/fatigue`}>{ledgerNum(d.fatigue, nd) + unit}</Explain> a day. Your normal <Explain path={`ledger/${name}/fitness`}>{ledgerNum(d.fitness, nd) + unit}</Explain>.</p>
              <Sparkline data={d.spark} color={`var(--${name})`} label={LEDGER_LABEL[name]} decimals={nd} unit={unit} />
            </div>;
          })}
        </div>
      </details>
    </section>
  );
}
