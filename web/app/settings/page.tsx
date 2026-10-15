import Link from "next/link";
import AccountCard from "@/components/AccountCard";
import AppearanceCard from "@/components/AppearanceCard";
import CoachSettings from "@/components/CoachSettings";
import NamesCard from "@/components/NamesCard";
import EngineDown from "@/components/EngineDown";
import CorosCard from "@/components/CorosCard";
import GarminCard from "@/components/GarminCard";
import StravaCard from "@/components/StravaCard";
import ReadinessSettings from "@/components/ReadinessSettings";
import SourceCard from "@/components/SourceCard";
import SyncCard from "@/components/SyncCard";
import ThresholdsCard from "@/components/ThresholdsCard";
import UploadCard from "@/components/UploadCard";
import WatchCard from "@/components/WatchCard";
import ZonesCard from "@/components/ZonesCard";
import { engine, engineFailed } from "@/lib/api";
import type { Account, Settings } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let s: Settings;
  let account: Account;
  try {
    [s, account] = await Promise.all([engine<Settings>("/api/settings"), engine<Account>("/api/account")]);
  } catch (e) {
    return <EngineDown message={engineFailed(e)} />;
  }

  return (
    <>
      <header className="page-head">
        <h1 className="title">Settings</h1>
        <div className="actions" style={{ paddingBottom: 6 }}>
          <Link href="/status">Status</Link>
          <Link href="/welcome">Setup guide</Link>
        </div>
      </header>
      <ThresholdsCard current={s.thresholds} inferred={s.inferred} />
      {s.thresholds ? <ZonesCard zones={s.thresholds.zones} /> : null}
      <div className="row">
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <h2 className="label" style={{ margin: "4px 0 -8px" }}>Data sources</h2>
          <SyncCard sources={s.sources} />
          <SourceCard source={s.source} />
          {s.sources.garmin.available ? <GarminCard garmin={s.sources.garmin} /> : null}
          {s.sources.coros.available ? <CorosCard coros={s.sources.coros} /> : null}
          {s.sources.strava_web?.available ? <StravaCard strava={s.sources.strava_web} /> : null}
          <WatchCard watch={s.sources.watch} />
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
          <NamesCard />
          <AccountCard account={account} />
        </div>
      </div>
    </>
  );
}
