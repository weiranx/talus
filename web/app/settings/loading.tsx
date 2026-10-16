import { Skel, SkelCard, SkelPage } from "@/components/Loading";
import "./settings.css";

export default function SettingsLoading() {
  return (
    <SkelPage label="Loading settings">
      <div className="settings-page">
        <header className="page-head settings-header">
          <div style={{ display: "grid", gap: 12, maxWidth: "100%" }}>
            <Skel w={120} h={12} />
            <Skel w={150} h={32} r={8} />
            <Skel w="min(360px, 100%)" h={16} />
          </div>
        </header>
        <div className="settings-layout">
          <div className="settings-nav">
            {[0, 1, 2].map((i) => <Skel key={i} w={140} h={44} r={10} />)}
          </div>
          <div className="settings-content">
            <div className="settings-section">
              <div style={{ display: "grid", gap: 10 }}><Skel w={180} h={26} /><Skel w="80%" h={14} /></div>
              <div className="settings-group">
                <SkelCard label={130} lines={3}><Skel w="80%" h={13} /><Skel w="50%" h={13} /><Skel w={120} h={44} r={12} /></SkelCard>
                {[0, 1, 2, 3, 4, 5].map((i) => (
                  <div className="settings-connection" key={i} style={{ padding: "20px 22px" }}><Skel h={18} /></div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </SkelPage>
  );
}
