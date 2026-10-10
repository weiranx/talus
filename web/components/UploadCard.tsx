"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { when } from "@/components/SourceCard";
import type { LastImport, Settings } from "@/lib/types";

function importLine(r: LastImport): string {
  if (r.error) return `Failed ${when(r.at)}: ${r.error}`;
  const parts = [`${r.new} new ${r.new === 1 ? "activity" : "activities"}`];
  if (r.duplicates) parts.push(`${r.duplicates} already here from another source`);
  if (r.listed - r.new - r.duplicates > 0) parts.push(`${r.listed - r.new - r.duplicates} already imported`);
  if (r.wellness_days) parts.push(`${r.wellness_days} days of resting HR and sleep`);
  if (r.no_data) parts.push(`${r.no_data} workouts with no route or heart rate skipped`);
  if (r.failed.length) parts.push(`${r.failed.length} files unreadable`);
  return `${r.source === "apple_health" ? "Apple Health export" : "Files"}, ${when(r.at)}: ${parts.join(", ")}.`;
}

export default function UploadCard({ upload }: { upload: Settings["sources"]["upload"] }) {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lines, setLines] = useState<{ ok: boolean; text: string }[]>([]);

  async function send(files: FileList | null) {
    if (!files?.length) return;
    const out: { ok: boolean; text: string }[] = [];
    for (const file of Array.from(files)) {
      if (file.size > upload.max_mb * 1024 * 1024) {
        out.push({ ok: false, text: `${file.name}: over ${upload.max_mb} MB.` });
        continue;
      }
      setBusy(file.name.endsWith(".zip") && file.size > 20e6 ? `Reading ${file.name}. A big Health export takes a few minutes.` : `Reading ${file.name}`);
      const res = await fetch(`/api/import?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file }).catch(() => null);
      const body = res ? await res.json().catch(() => ({})) : null;
      if (!res) out.push({ ok: false, text: `${file.name}: could not reach the engine.` });
      else if (!res.ok) out.push({ ok: false, text: `${file.name}: ${body?.detail ?? `upload failed (${res.status})`}` });
      else out.push({ ok: !body.error, text: `${file.name}: ${importLine(body)}` });
      setLines([...out]);
    }
    setBusy(null);
    setLines(out);
    if (input.current) input.current.value = "";
    router.refresh();
  }

  return (
    <section className="card">
      <h2 className="label">Upload files</h2>
      <div className="muted">
        FIT, GPX or TCX files from any watch or app, one at a time or zipped together. Or the Apple Health export: in the
        Health app, tap your picture, then Export All Health Data, and upload the export.zip. It brings in workouts with
        their routes and heart rate, plus resting heart rate and sleep for nights no other source has.
      </div>
      <div className="actions">
        <input ref={input} type="file" multiple accept=".fit,.gpx,.tcx,.zip,.gz" style={{ display: "none" }}
          onChange={(e) => send(e.target.files)} />
        <button className="btn" onClick={() => input.current?.click()} disabled={busy !== null} aria-busy={busy !== null}>{busy ? "Uploading" : "Choose files"}</button>
      </div>
      {busy ? <div className="muted" role="status">{busy}</div> : null}
      {lines.map((l, i) => <div key={i} className={l.ok ? "ok-text" : "error-text"}>{l.text}</div>)}
      {!lines.length && upload.last_import ? <div className="muted">Last: {importLine(upload.last_import)}</div> : null}
      <div className="muted">
        Activities you already have from another source are kept once. Not connected: Strava, whose API terms bar apps
        with AI coaching, and COROS, which opens its API only to approved partners. Export files from either and upload
        them here.
      </div>
    </section>
  );
}
