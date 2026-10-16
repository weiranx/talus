import Link from "next/link";
import type { ReactNode } from "react";
import "./settings.css";
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
    <div className="settings-page">
      <header className="page-head settings-header">
        <div>
          <h1 className="title">Settings</h1>
          <p className="settings-intro">Your training inputs, connected sources, and personal preferences.</p>
        </div>
        <div className="actions settings-help">
          <Link href="/status">System status</Link>
          <Link href="/welcome">Setup guide <span aria-hidden>↗</span></Link>
        </div>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          <p className="settings-eyebrow">On this page</p>
          <a href="#sources">Data sources</a>
          <a href="#training">Training & coach</a>
          <a href="#account">Account & display</a>
          <p className="settings-nav-note">Save changes beside each setting.</p>
        </nav>
        <div className="settings-content">
          <SettingsSection id="sources" title="Data sources" description="Sync your activities and manage connections in one place.">
            <div className="settings-group">
              <SyncCard sources={s.sources} />
              <ConnectionRow title="intervals.icu" status={s.source.key_set ? "Connected" : "Not connected"}>
                <SourceCard source={s.source} />
              </ConnectionRow>
              {s.sources.garmin.available ? (
                <ConnectionRow title="Garmin Connect" status={s.sources.garmin.mfa_pending ? "Verification needed" : s.sources.garmin.connected ? "Connected" : "Not connected"}>
                  <GarminCard garmin={s.sources.garmin} />
                </ConnectionRow>
              ) : null}
              {s.sources.coros.available ? (
                <ConnectionRow title="COROS" status={s.sources.coros.connected ? "Connected" : "Not connected"}>
                  <CorosCard coros={s.sources.coros} />
                </ConnectionRow>
              ) : null}
              {s.sources.strava_web?.available ? (
                <ConnectionRow title="Strava" status={s.sources.strava_web.enabled ? "On" : "Off"}>
                  <StravaCard strava={s.sources.strava_web} />
                </ConnectionRow>
              ) : null}
              <ConnectionRow title="Garmin watch" status={s.sources.watch.last_attempt?.error ? "Needs attention" : s.sources.watch.enabled ? "On" : "Off"}>
                <WatchCard watch={s.sources.watch} />
              </ConnectionRow>
              <ConnectionRow title="Import files" status="FIT, GPX, TCX & Health">
                <UploadCard upload={s.sources.upload} />
              </ConnectionRow>
            </div>
          </SettingsSection>
          <SettingsSection id="training" title="Training & coach" description="The inputs behind your training decisions, and how your coach uses them.">
            <div className="settings-group">
              <ThresholdsCard current={s.thresholds} inferred={s.inferred} />
              {s.thresholds ? <ZonesCard zones={s.thresholds.zones} /> : null}
              <ReadinessSettings readiness={s.readiness} />
              <CoachSettings coach={s.coach} />
              <NamesCard />
            </div>
          </SettingsSection>
          <SettingsSection id="account" title="Account & display" description="Your appearance, units, sign-in, and personal data.">
            <div className="settings-group">
              <AppearanceCard />
              <section className="card">
                <h3 className="label">Units</h3>
                <p>Miles for distance · Feet for elevation gain and loss</p>
                <p className="muted">Kilometers and meters are not available yet.</p>
              </section>
              <AccountCard account={account} />
            </div>
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

function SettingsSection({ id, title, description, children }: {
  id: string; title: string; description: string; children: ReactNode;
}) {
  return (
    <section id={id} className="settings-section" aria-labelledby={`${id}-heading`} tabIndex={-1}>
      <header className="settings-section-head">
        <div>
          <h2 id={`${id}-heading`}>{title}</h2>
          <p>{description}</p>
        </div>
      </header>
      {children}
    </section>
  );
}

function ConnectionRow({ title, status, children }: { title: string; status: string; children: ReactNode }) {
  return (
    <details className="settings-connection">
      <summary>
        <span className="settings-connection-title">{title}</span>
        <span className="settings-connection-status">{status}</span>
        <span className="settings-connection-chevron" aria-hidden>›</span>
      </summary>
      {children}
    </details>
  );
}
