"use client";

import { useEffect, useState } from "react";

/** The intervals.icu OAuth button. Only render it when Settings' source.oauth_available is true.
 * intervals.icu sends the browser back to `next` (default /settings); on failure the URL carries
 * ?intervals_error=..., which this component shows. */
export default function ConnectIntervals({ next = "/settings", label = "Connect intervals.icu", hint }:
  { next?: string; label?: string; hint?: string | null }) {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(new URLSearchParams(window.location.search).get("intervals_error"));
  }, []);
  return (
    <>
      <div className="actions">
        <a className="btn" href={`/api/auth/intervals/start?next=${encodeURIComponent(next)}`}>{label}</a>
        {hint === null ? null : <span className="muted">{hint ?? "Approve Talus on intervals.icu to read your activities and wellness."}</span>}
      </div>
      {error ? <div className="error-text" role="status">{error}</div> : null}
    </>
  );
}
