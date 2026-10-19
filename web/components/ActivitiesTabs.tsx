import Link from "next/link";
import LinkPending from "@/components/LinkPending";

// Activity history, routes, and evidence-backed performance comparisons.
export default function ActivitiesTabs({ current }: { current: "list" | "heatmap" | "progress" }) {
  return (
    <nav className="seg-tabs" aria-label="Activities view">
      <Link href="/activities" aria-current={current === "list" ? "true" : undefined}>List<LinkPending /></Link>
      <Link href="/activities/heatmap" aria-current={current === "heatmap" ? "true" : undefined}>Heatmap<LinkPending /></Link>
      <Link href="/activities/progress" aria-current={current === "progress" ? "true" : undefined}>Progress<LinkPending /></Link>
    </nav>
  );
}
