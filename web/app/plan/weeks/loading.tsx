import { SkelHead, SkelLines, SkelPage, SkelTable } from "@/components/Loading";

export default function FullPlanLoading() {
  return (
    <SkelPage label="Loading the full plan">
      <SkelHead eyebrow title={320} />
      <section className="card">
        <SkelLines n={2} />
        <SkelTable rows={14} cols={6} />
      </section>
    </SkelPage>
  );
}
