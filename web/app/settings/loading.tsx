import { Skel, SkelCard, SkelPage } from "@/components/Loading";

function Stats({ n }: { n: number }) {
  return (
    <div className="stat-grid">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6 }}><Skel w={60} h={12} /><Skel w={56} h={24} /></div>
      ))}
    </div>
  );
}

export default function SettingsLoading() {
  return (
    <SkelPage label="Loading settings">
      <header className="page-head"><Skel w={150} h={32} r={8} /></header>
      <SkelCard label={160}><Stats n={4} /><Skel w="55%" h={12} /><Skel w={150} h={44} r={12} /></SkelCard>
      <SkelCard label={120}>{[0, 1, 2].map((i) => <Skel key={i} h={40} r={8} />)}</SkelCard>
      <div className="row">
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SkelCard label={130} lines={3}><Skel w="80%" h={13} /><Skel w="50%" h={13} /><Skel w={120} h={44} r={12} /></SkelCard>
          <SkelCard label={110} lines={2} />
          <SkelCard label={100} lines={2} />
        </div>
        <div style={{ flex: "1 1 380px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SkelCard label={90}>{[0, 1, 2].map((i) => <Skel key={i} h={44} r={10} />)}<Skel w={180} h={44} r={12} /></SkelCard>
          <SkelCard label={100} lines={3} />
        </div>
      </div>
    </SkelPage>
  );
}
