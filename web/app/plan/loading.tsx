import { Skel, SkelCard, SkelHead, SkelLines, SkelPage, SkelTable } from "@/components/Loading";

export default function PlanLoading() {
  return (
    <SkelPage label="Loading your plan">
      <SkelHead eyebrow title={260} right={240} />
      <SkelCard label={90}>
        <div className="table-wrap">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(92px, 1fr))", gap: 4, minWidth: 660 }}>
            {Array.from({ length: 35 }, (_, i) => <Skel key={i} h={92} r={10} />)}
          </div>
        </div>
      </SkelCard>
      <div className="row">
        <div style={{ flex: "2 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SkelCard label={100}>{[0, 1, 2].map((i) => <Skel key={i} h={10} r={5} />)}</SkelCard>
          <SkelCard label={110}><SkelTable rows={6} cols={5} /></SkelCard>
          <SkelCard label={160}><SkelLines n={4} /></SkelCard>
        </div>
        <div style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <SkelCard label={100} lines={3} />
          <SkelCard label={60}><Skel h={120} r={10} /><Skel h={44} r={10} /></SkelCard>
        </div>
      </div>
    </SkelPage>
  );
}
