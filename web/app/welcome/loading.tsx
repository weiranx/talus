import { Skel, SkelCard, SkelHead, SkelPage } from "@/components/Loading";

export default function WelcomeLoading() {
  return (
    <SkelPage label="Loading setup">
      <div style={{ maxWidth: 760, margin: "0 auto", width: "100%", display: "flex", flexDirection: "column", gap: 16 }}>
        <SkelHead eyebrow title={260} />
        <Skel w="80%" h={24} />
        <SkelCard label={110} lines={4} />
        <SkelCard label={90} lines={3} />
      </div>
    </SkelPage>
  );
}
