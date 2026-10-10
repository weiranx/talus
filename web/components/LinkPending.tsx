"use client";

import { useLinkStatus } from "next/link";

// A small spinner inside a Link while its page loads. It always takes its space, so the link never shifts.
export default function LinkPending() {
  const { pending } = useLinkStatus();
  return <span className="link-pending" data-pending={pending ? "true" : undefined} aria-hidden />;
}
