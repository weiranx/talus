"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { mark } from "./steps";

// Leaves setup for Today. Today then lists whatever is still missing. Also records that the flow
// was opened, so it resumes here rather than starting over.
export default function SkipSetup({ started }: { started: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!started) void mark("start");
  }, [started]);
  return (
    <button className="link-btn" style={{ fontSize: 13 }} disabled={busy} onClick={async () => {
      setBusy(true);
      await mark("finish");
      router.push("/");
      router.refresh();
    }}>Skip setup, go to Today</button>
  );
}
