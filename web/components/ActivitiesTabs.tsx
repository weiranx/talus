import Link from "next/link";
import LinkPending from "@/components/LinkPending";

// Activities has two views: the list, and every route on one map.
export default function ActivitiesTabs({ current }: { current: "list" | "heatmap" }) {
  return (
    <nav className="seg-tabs" aria-label="Activities view">
      <Link href="/activities" aria-current={current === "list" ? "true" : undefined}>List<LinkPending /></Link>
      <Link href="/activities/heatmap" aria-current={current === "heatmap" ? "true" : undefined}>Heatmap<LinkPending /></Link>
    </nav>
  );
}
