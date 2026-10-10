import { Skel, SkelCard, SkelPage, SkelTable } from "@/components/Loading";

export default function LoadLoading() {
  return (
    <SkelPage label="Loading your load charts">
      <header className="page-head">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Skel w={100} h={32} r={8} />
          <Skel w={360} h={13} />
        </div>
        <Skel w={190} h={38} r={10} />
      </header>
      {[0, 1, 2, 3].map((i) => (
        <section key={i} className="card">
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}><Skel w={90} h={17} /><Skel w={64} h={24} r={999} /></div>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}><Skel w={200} h={20} /><Skel w={260} h={12} /></div>
          </div>
          <Skel h={220} r={10} />
          <Skel w="45%" h={12} />
        </section>
      ))}
      <div className="row">
        <SkelCard label={140} style={{ flex: "1 1 380px" }}><Skel h={200} r={10} /></SkelCard>
        <SkelCard label={180} style={{ flex: "1 1 380px" }}><SkelTable rows={6} cols={6} /></SkelCard>
      </div>
    </SkelPage>
  );
}
