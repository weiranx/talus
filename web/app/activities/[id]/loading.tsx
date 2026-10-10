import { Skel, SkelCard, SkelLines, SkelPage, SkelTable } from "@/components/Loading";

export default function ActivityLoading() {
  return (
    <SkelPage label="Loading the activity">
      <header className="page-head">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Skel w={220} h={13} />
          <Skel w={280} h={32} r={8} />
          <Skel w={240} h={13} />
        </div>
      </header>
      <section className="card">
        <div className="stat-grid">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="stat" style={{ display: "flex", flexDirection: "column", gap: 6 }}><Skel w={60} h={12} /><Skel w={80} h={24} /></div>
          ))}
        </div>
        <div className="divider" />
        <div className="stat-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6 }}><Skel w={70} h={14} /><Skel w={56} h={20} /><Skel w="70%" h={12} /></div>
          ))}
        </div>
      </section>
      <SkelCard label={100}><SkelLines n={4} /></SkelCard>
      <SkelCard label={120}><Skel h={360} r={12} /><Skel h={160} r={10} /></SkelCard>
      <SkelCard label={110}><SkelTable rows={3} cols={3} /></SkelCard>
    </SkelPage>
  );
}
