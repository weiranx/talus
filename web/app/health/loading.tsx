import { Skel, SkelPage } from "@/components/Loading";

export default function HealthLoading() {
  return (
    <SkelPage label="Loading your health history">
      <header className="page-head">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Skel w={110} h={32} r={8} />
          <Skel w={360} h={13} />
        </div>
        <Skel w={230} h={38} r={10} />
      </header>
      {[0, 1, 2].map((i) => (
        <section key={i} className="card">
          <Skel w={120} h={17} />
          <Skel h={180} r={10} />
          <Skel w="45%" h={12} />
        </section>
      ))}
    </SkelPage>
  );
}
