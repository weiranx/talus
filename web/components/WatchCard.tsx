"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Pill from "@/components/Pill";
import { when } from "@/components/SourceCard";
import { pick, WatchSession, type Progress, type WatchFile } from "@/lib/garmin";

const FIRST_SYNC_DAYS = 14;

type Added = { id: number; name: string | null; start_time: string | null; distance_m: number | null; gain_m: number | null };

// Where this browser stands: Web Bluetooth that can reach a Garmin watch, a Mac (Chrome there
// refuses the watch), or no Web Bluetooth at all (Safari, Firefox, any iPhone browser).
type Reach = "ok" | "mac" | "none";

function reach(): Reach {
  if (!WatchSession.supported()) return "none";
  const mac = /Mac/.test(navigator.platform) && navigator.maxTouchPoints < 2;
  return mac ? "mac" : "ok";
}

function runLine(a: Added): string {
  const parts: string[] = [];
  if (a.start_time) parts.push(new Date(a.start_time).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }));
  if (a.name) parts.push(a.name);
  if (a.distance_m != null) parts.push(`${(a.distance_m / 1609.344).toFixed(1)} mi`);
  if (a.gain_m != null) parts.push(`${Math.round(a.gain_m * 3.28084).toLocaleString("en-US")} ft up`);
  return parts.join(" · ");
}

function label(f: WatchFile): string {
  const kind = { FIT_TYPE_4: "Activity", FIT_TYPE_32: "Monitoring", FIT_TYPE_49: "Sleep", FIT_TYPE_68: "HRV status", FIT_TYPE_44: "Metrics" }[f.kind] ?? f.kind;
  return f.time ? `${kind}, ${f.time.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : kind;
}

export default function WatchCard({ lastSync }: { lastSync: string | null }) {
  const router = useRouter();
  const [where, setWhere] = useState<Reach>("ok");
  const [tryHere, setTryHere] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [lines, setLines] = useState<{ ok: boolean; text: string }[]>([]);
  const [added, setAdded] = useState<Added[]>([]);

  useEffect(() => setWhere(reach()), []);

  // Tells the engine how it went, so "last sync" and the status page stay current even when the
  // watch had nothing new, and a failed sync shows as the source's error.
  const done = (n: number, error: string | null) =>
    fetch("/api/import/watch/done", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ new: n, error: error?.slice(0, 500) ?? null }) }).catch(() => null);

  async function sync() {
    setBusy(true);
    setLines([]);
    setAdded([]);
    let added = 0;
    const out: { ok: boolean; text: string }[] = [];
    const say = (ok: boolean, text: string) => { out.push({ ok, text }); setLines([...out]); };
    const session = new WatchSession(setProgress);
    try {
      const state = await fetch("/api/import/watch").then((r) => r.json()) as { files: string[]; last_sync: string | null; taken?: [string, string][] };
      await session.connect();
      say(true, `Connected to ${session.info.model || "your watch"} (software ${session.info.firmware}).`);
      const files = await session.list();
      // Activities from the last two weeks, or from two days before the last sync if that is
      // earlier. Files already imported are skipped by key, so an interrupted sync resumes.
      const twoWeeks = Date.now() - FIRST_SYNC_DAYS * 86400e3;
      const since = new Date(state.last_sync ? Math.min(twoWeeks, new Date(state.last_sync).getTime() - 2 * 86400e3) : twoWeeks);
      const todo = pick(files, new Set(state.files), since, state.taken ?? []);
      const already = pick(files, new Set(state.files), since).length - todo.length;
      if (!todo.length) say(true, `${files.length} files on the watch, nothing new${already ? ` (${already} already in Talus from another source)` : ""}.`);
      let failed = 0;
      for (const [i, f] of todo.entries()) {
        setProgress({ phase: `Downloading ${label(f)}`, done: i, total: todo.length, bytes: 0 });
        const fit = await session.fetch(f, (bytes) => setProgress({ phase: `Downloading ${label(f)}`, done: i, total: todo.length, bytes }));
        const name = `${f.key}.fit`;
        const res = await fetch(`/api/import?name=${encodeURIComponent(name)}&watch_file=${encodeURIComponent(f.key)}`,
          { method: "POST", body: fit as BodyInit }).catch(() => null);
        const body = res ? await res.json().catch(() => ({})) : null;
        if (!res?.ok || body?.error || body?.failed?.length) {
          failed += 1;
          say(false, `${label(f)}: ${body?.error ?? body?.failed?.[0] ?? body?.detail ?? "import failed"}`);
        } else {
          added += body.new ?? 0;
          if (body.added?.length) setAdded((was) => [...body.added, ...was].sort((a, b) => (b.start_time ?? "").localeCompare(a.start_time ?? "")));
        }
      }
      if (todo.length) say(true, `Pulled ${todo.length} ${todo.length === 1 ? "file" : "files"}: ${added} new ${added === 1 ? "activity" : "activities"}.`);
      await done(added, failed ? `${failed} of ${todo.length} files failed.` : null);
    } catch (e) {
      const msg = (e as Error).message;
      if ((e as Error).name !== "NotFoundError") await done(added, msg);
      // Chrome on a Mac refuses to connect to Garmin watches; Windows, Android and the iPhone app work.
      if (/unsupported device/i.test(msg)) say(false, "This browser can't connect to the watch (Chrome on a Mac refuses it). Sync from the Talus iPhone app instead, or Chrome on Windows or Android.");
      else say(false, (e as Error).name === "NotFoundError"
        ? "No watch chosen. If it wasn't in the list, your phone is holding its connection: turn Bluetooth off in the phone's Settings (not Control Center) and try again." : msg);
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
        Pulls new activities straight off your watch over Bluetooth, without Garmin&apos;s servers. Nothing on the
        watch is changed or deleted. Sleep, HRV and resting heart rate don&apos;t come from the watch, since Garmin
        Connect clears those files as it syncs; they still come from intervals.icu or Apple Health.
      </div>
      {where === "ok" || tryHere ? (
        <>
          <div className="muted">
            Turn off Bluetooth in your phone&apos;s Settings first (the watch stays hidden while the phone holds it)
            and keep the watch close.
          </div>
          <div className="actions">
            <button className="btn" onClick={sync} disabled={busy} aria-busy={busy}>{busy ? "Syncing" : "Sync from watch"}</button>
          </div>
        </>
      ) : (
        <div>
          <strong>Sync from the Talus iPhone app.</strong>{" "}
          <span className="muted">
            {where === "mac" ? "Chrome on a Mac can't connect to Garmin watches. " : "This browser can't use Bluetooth. "}
            The iPhone app syncs alongside Garmin Connect, with no pairing, and does it on its own whenever you open
            Talus. Chrome or Edge on Windows or Android can sync from here too.
          </span>
          {where === "mac" ? (
            <div className="actions"><button className="link-btn" onClick={() => setTryHere(true)}>Try this browser anyway</button></div>
          ) : null}
        </div>
      )}
      {p ? (
        <div className="muted" role="status">
          {p.phase}
          {p.total ? ` (${(p.done ?? 0) + 1} of ${p.total}${p.bytes ? `, ${Math.round(p.bytes / 1024)} KB` : ""})` : ""}
        </div>
      ) : null}
      {lines.map((l, i) => <div key={i} className={l.ok ? "ok-text" : "error-text"}>{l.text}</div>)}
      {added.length ? (
        <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
          {added.map((a) => <li key={a.id}><a href={`/activities/${a.id}`}>{runLine(a)}</a></li>)}
        </ul>
      ) : null}
      {!lines.length && lastSync ? <div className="muted">Last sync {when(lastSync)}.</div> : null}
      {!lastSync && !busy ? <div className="muted">The first sync brings in the last {FIRST_SYNC_DAYS} days of activities, about half a minute each.</div> : null}
    </section>
  );
}
