"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Pill from "@/components/Pill";
import { when } from "@/components/SourceCard";
import { pick, WatchSession, type Progress, type WatchFile } from "@/lib/garmin";

const FIRST_SYNC_DAYS = 14;

function label(f: WatchFile): string {
  const kind = { FIT_TYPE_4: "Activity", FIT_TYPE_32: "Monitoring", FIT_TYPE_49: "Sleep", FIT_TYPE_68: "HRV status", FIT_TYPE_44: "Metrics" }[f.kind] ?? f.kind;
  return f.time ? `${kind}, ${f.time.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : kind;
}

export default function WatchCard({ lastSync }: { lastSync: string | null }) {
  const router = useRouter();
  const [supported, setSupported] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [lines, setLines] = useState<{ ok: boolean; text: string }[]>([]);

  useEffect(() => setSupported(WatchSession.supported()), []);

  async function sync() {
    setBusy(true);
    setLines([]);
    const out: { ok: boolean; text: string }[] = [];
    const say = (ok: boolean, text: string) => { out.push({ ok, text }); setLines([...out]); };
    const session = new WatchSession(setProgress);
    try {
      const state = await fetch("/api/import/watch").then((r) => r.json()) as { files: string[]; last_sync: string | null };
      await session.connect();
      say(true, `Connected to ${session.info.model || "your watch"} (software ${session.info.firmware}).`);
      const files = await session.list();
      // Activities from the last two weeks, or from two days before the last sync if that is
      // earlier. Files already imported are skipped by key, so an interrupted sync resumes.
      const window = Date.now() - FIRST_SYNC_DAYS * 86400e3;
      const since = new Date(state.last_sync ? Math.min(window, new Date(state.last_sync).getTime() - 2 * 86400e3) : window);
      const todo = pick(files, new Set(state.files), since);
      if (!todo.length) say(true, `${files.length} files on the watch, nothing new.`);
      let added = 0;
      for (const [i, f] of todo.entries()) {
        setProgress({ phase: `Downloading ${label(f)}`, done: i, total: todo.length, bytes: 0 });
        const fit = await session.fetch(f, (bytes) => setProgress({ phase: `Downloading ${label(f)}`, done: i, total: todo.length, bytes }));
        const name = `${f.key}.fit`;
        const res = await fetch(`/api/import?name=${encodeURIComponent(name)}&watch_file=${encodeURIComponent(f.key)}`,
          { method: "POST", body: fit as BodyInit }).catch(() => null);
        const body = res ? await res.json().catch(() => ({})) : null;
        if (!res?.ok || body?.error || body?.failed?.length) say(false, `${label(f)}: ${body?.error ?? body?.failed?.[0] ?? body?.detail ?? "import failed"}`);
        else added += body.new ?? 0;
      }
      if (todo.length) say(true, `Pulled ${todo.length} ${todo.length === 1 ? "file" : "files"}: ${added} new ${added === 1 ? "activity" : "activities"}.`);
    } catch (e) {
      const msg = (e as Error).message;
      say(false, (e as Error).name === "NotFoundError" ? "No watch chosen." : msg);
    } finally {
      session.disconnect();
      setProgress(null);
      setBusy(false);
      router.refresh();
    }
  }

  const p = progress;
  return (
    <section className="card">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label">Garmin watch</h2>
        <div className="actions" style={{ gap: 6 }}>
          <Pill tone="warn">Experimental</Pill>
          {lastSync ? <Pill tone="good">Synced</Pill> : <Pill tone="neutral">Not synced yet</Pill>}
        </div>
      </div>
      <div className="muted">
        Pulls activities, sleep, HRV and resting heart rate straight off your watch over Bluetooth, without Garmin&apos;s
        servers. Nothing on the watch is changed or deleted. Your watch stays hidden while it&apos;s connected to your
        phone, so turn off Bluetooth on the phone first, and keep the watch close. Overnight files only stay on the
        watch until Garmin Connect syncs them, so sync here first for sleep and HRV.
      </div>
      {supported ? (
        <div className="actions">
          <button className="btn" onClick={sync} disabled={busy} aria-busy={busy}>{busy ? "Syncing" : "Sync from watch"}</button>
        </div>
      ) : (
        <div className="error-text">This browser can&apos;t use Bluetooth. Use Chrome or Edge on a computer, or the Talus iPhone app.</div>
      )}
      {p ? (
        <div className="muted" role="status">
          {p.phase}
          {p.total ? ` (${(p.done ?? 0) + 1} of ${p.total}${p.bytes ? `, ${Math.round(p.bytes / 1024)} KB` : ""})` : ""}
        </div>
      ) : null}
      {lines.map((l, i) => <div key={i} className={l.ok ? "ok-text" : "error-text"}>{l.text}</div>)}
      {!lines.length && lastSync ? <div className="muted">Last sync {when(lastSync)}.</div> : null}
      {!lastSync && !busy ? <div className="muted">The first sync brings in the last {FIRST_SYNC_DAYS} days of activities, about half a minute each.</div> : null}
    </section>
  );
}
