import { Skel, SkelCard, SkelPage } from "@/components/Loading";

export default function StatusLoading() {
  return (
    <SkelPage label="Loading status">
      <header className="page-head"><Skel w={120} h={32} r={8} /></header>
      <SkelCard label={90} lines={3} />
      <div className="row">
        <SkelCard label={110} lines={4} style={{ flex: "1 1 380px" }} />
        <SkelCard label={90} lines={4} style={{ flex: "1 1 380px" }} />
      </div>
    </SkelPage>
  );
}
