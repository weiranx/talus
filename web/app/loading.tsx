import { Skel, SkelCard, SkelHead, SkelLines, SkelPage, SkelTable } from "@/components/Loading";

// Today, while the engine answers. Same grid as the page.
export default function TodayLoading() {
  return (
    <SkelPage label="Loading today">
      <SkelHead eyebrow title={120} right={220} />
      <div className="row">
        <div style={{ flex: "2 1 420px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card dark" style={{ padding: "22px 24px 18px", minHeight: 230 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <Skel w={100} h={12} /><Skel w={56} h={24} r={999} />
            </div>
            <Skel w="70%" h={30} r={8} />
            <SkelLines n={2} h={14} />
            <Skel w="50%" h={13} />
          </section>
          <SkelCard label={90} lines={2} />
        </div>
        <div style={{ flex: "1 1 300px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card" style={{ gap: 14 }}>
            <Skel w={80} h={12} />
            {[0, 1].map((i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}><Skel w={70} h={17} /><Skel w="80%" h={12} /></div>
                <Skel w={64} h={24} r={999} />
              </div>
            ))}
          </section>
          <SkelCard label={90} lines={2} />
        </div>
      </div>
      <div className="row">
        {[0, 1, 2, 3].map((i) => (
          <section key={i} className="card" style={{ flex: "1 1 220px", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}><Skel w={80} h={17} /><Skel w={60} h={24} r={999} /></div>
            <Skel w="75%" h={22} />
            <Skel w="90%" h={12} />
            <Skel h={48} r={8} />
          </section>
        ))}
      </div>
      <div className="row">
        <SkelCard label={160} style={{ flex: "2 1 480px" }}><SkelTable rows={4} cols={5} /></SkelCard>
        <SkelCard label={120} style={{ flex: "1 1 300px", alignSelf: "flex-start" }}><Skel h={14} r={4} /><Skel w="60%" h={12} /></SkelCard>
      </div>
    </SkelPage>
  );
}
