"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";

export default function RefreshToday() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button className="today-refresh" type="button" disabled={pending} aria-busy={pending}
      onClick={() => startTransition(() => router.refresh())}>
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.7-1L20 9M4 15l2.2 3A7 7 0 0 0 17.9 17" />
      </svg>
      <span>{pending ? "Refreshing…" : "Refresh today"}</span>
    </button>
  );
}
