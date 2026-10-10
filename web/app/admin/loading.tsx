import { Skel, SkelCard, SkelPage, SkelTable } from "@/components/Loading";

export default function AdminLoading() {
  return (
    <SkelPage label="Loading admin">
      <header className="page-head"><Skel w={120} h={32} r={8} /></header>
      <section className="card">
        <Skel w={90} h={12} />
        <SkelTable rows={3} cols={8} minWidth={860} />
      </section>
      <div className="row">
        <SkelCard label={90} lines={2} style={{ flex: "1 1 380px" }} />
        <SkelCard label={110} lines={4} style={{ flex: "1 1 380px" }} />
      </div>
    </SkelPage>
  );
}
