"use client";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

export default function ProgressRefresh() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return <button type="button" disabled={pending} aria-busy={pending}
    onClick={() => startTransition(() => router.refresh())}>
    {pending ? "Refreshing…" : "Refresh comparisons"}
  </button>;
}
