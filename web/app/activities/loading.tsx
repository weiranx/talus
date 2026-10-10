import { Skel, SkelPage, SkelTable } from "@/components/Loading";

export default function ActivitiesLoading() {
  return (
    <SkelPage label="Loading activities">
      <Skel w={170} h={32} r={8} />
      <section className="card">
        <SkelTable rows={12} cols={10} minWidth={720} />
        <Skel w="70%" h={12} />
      </section>
    </SkelPage>
  );
}
