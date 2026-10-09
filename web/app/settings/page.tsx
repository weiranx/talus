import AccountCard from "@/components/AccountCard";
import CoachSettings from "@/components/CoachSettings";
import EngineDown from "@/components/EngineDown";
import SourceCard from "@/components/SourceCard";
import ThresholdsCard from "@/components/ThresholdsCard";
import { engine, EngineError } from "@/lib/api";
import type { Account, Settings } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let s: Settings;
  let account: Account;
  try {
    [s, account] = await Promise.all([engine<Settings>("/api/settings"), engine<Account>("/api/account")]);
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }

  return (
    <>
      <header className="page-head">
        <h1 className="title">Settings</h1>
      </header>
      <ThresholdsCard current={s.thresholds} inferred={s.inferred} />
      <div className="row">
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SourceCard source={s.source} />
          <section className="card">
            <h2 className="label">Units</h2>
            <dl className="kv">
              <dt>Distance</dt>
              <dd>Miles</dd>
              <dt>Vert</dt>
              <dd>Feet, gain and loss</dd>
            </dl>
            <div className="muted">Kilometers and meters are not available yet.</div>
          </section>
        </div>
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <CoachSettings coach={s.coach} />
          <AccountCard account={account} />
        </div>
      </div>
    </>
  );
}
