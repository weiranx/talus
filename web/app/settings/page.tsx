import CoachSettings from "@/components/CoachSettings";
import EngineDown from "@/components/EngineDown";
import ReadinessSettings from "@/components/ReadinessSettings";
import SourceCard from "@/components/SourceCard";
import ThresholdsCard from "@/components/ThresholdsCard";
import ZonesCard from "@/components/ZonesCard";
import { engine, EngineError } from "@/lib/api";
import type { Settings } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let s: Settings;
  try {
    s = await engine<Settings>("/api/settings");
  } catch (e) {
    return <EngineDown message={e instanceof EngineError ? e.message : "Could not reach the engine."} />;
  }

  return (
    <>
      <header className="page-head">
        <h1 className="title">Settings</h1>
      </header>
      <ThresholdsCard current={s.thresholds} inferred={s.inferred} />
      {s.thresholds ? <ZonesCard zones={s.thresholds.zones} /> : null}
      <div className="row">
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SourceCard source={s.source} />
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
        </div>
        <div style={{ flex: "1 1 380px", minWidth: 0 }}>
          <CoachSettings coach={s.coach} />
        </div>
      </div>
    </>
  );
}
