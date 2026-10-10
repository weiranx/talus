import Link from "next/link";
import AccountCard from "@/components/AccountCard";
import AppearanceCard from "@/components/AppearanceCard";
import CoachSettings from "@/components/CoachSettings";
import EngineDown from "@/components/EngineDown";
import GarminCard from "@/components/GarminCard";
import ReadinessSettings from "@/components/ReadinessSettings";
import SourceCard from "@/components/SourceCard";
import ThresholdsCard from "@/components/ThresholdsCard";
import UploadCard from "@/components/UploadCard";
import ZonesCard from "@/components/ZonesCard";
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
        <Link href="/welcome" style={{ paddingBottom: 6 }}>Setup guide</Link>
      </header>
      <ThresholdsCard current={s.thresholds} inferred={s.inferred} />
      {s.thresholds ? <ZonesCard zones={s.thresholds.zones} /> : null}
      <div className="row">
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <h2 className="label" style={{ margin: "4px 0 -8px" }}>Data sources</h2>
          <SourceCard source={s.source} lastSync={s.sources.intervals.last_sync} />
          {s.sources.garmin.available ? <GarminCard garmin={s.sources.garmin} /> : null}
          <UploadCard upload={s.sources.upload} />
          <ReadinessSettings readiness={s.readiness} />
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
          <AppearanceCard />
        </div>
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <CoachSettings coach={s.coach} />
          <AccountCard account={account} />
        </div>
      </div>
    </>
  );
}
