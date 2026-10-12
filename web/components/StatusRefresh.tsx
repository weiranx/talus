"use client";

import { useRouter } from "next/navigation";
import { useEffect, useTransition } from "react";

// Reads the status again on a click, and every 15 seconds while something is moving (a history
// import, the activity read-ahead), so its progress shows without reloading.
export default function StatusRefresh({ live }: { live: boolean }) {
  const router = useRouter();
  const [pending, start] = useTransition();

  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => start(() => router.refresh()), 15000);
    return () => clearInterval(id);
  }, [live, router]);

  return (
    <button className="btn light outline" onClick={() => start(() => router.refresh())} disabled={pending} aria-busy={pending}>
      Refresh
    </button>
  );
}
