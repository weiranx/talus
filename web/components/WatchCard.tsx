"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Pill from "@/components/Pill";
import { pick, WatchSession, type Progress, type WatchFile } from "@/lib/garmin";
import { addedLine, when, type Added } from "@/lib/sync";
import type { Settings } from "@/lib/types";

const FIRST_SYNC_DAYS = 14;

// Where this browser stands: Web Bluetooth that can reach a Garmin watch, a Mac (Chrome there
// refuses the watch), or no Web Bluetooth at all (Safari, Firefox, any iPhone browser).
type Reach = "ok" | "mac" | "none";

function reach(): Reach {
  if (!WatchSession.supported()) return "none";
  const mac = /Mac/.test(navigator.platform) && navigator.maxTouchPoints < 2;
  return mac ? "mac" : "ok";
}

function label(f: WatchFile): string {
  const kind = { FIT_TYPE_4: "Activity", FIT_TYPE_32: "Monitoring", FIT_TYPE_49: "Sleep", FIT_TYPE_68: "HRV status", FIT_TYPE_44: "Metrics" }[f.kind] ?? f.kind;
  return f.time ? `${kind}, ${f.time.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}` : kind;
}

export default function WatchCard({ watch }: { watch: Settings["sources"]["watch"] }) {
  const router = useRouter();
  const lastSync = watch.last_sync;
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [where, setWhere] = useState<Reach | null>(null);
  const [tryHere, setTryHere] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [lines, setLines] = useState<{ ok: boolean; text: string }[]>([]);
  const [added, setAdded] = useState<Added[]>([]);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

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
    setMessage(null);
    let added = 0;
    // Mornings the overnight files filled: a night's sleep and HRV files arrive separately.
    const nights = new Set<string>();
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
          for (const d of (body.wellness_dates ?? []) as string[]) nights.add(d);
          if (body.added?.length) setAdded((was) => [...body.added, ...was].sort((a, b) => (b.start_time ?? "").localeCompare(a.start_time ?? "")));
        }
      }
      if (todo.length) say(true, `Pulled ${todo.length} ${todo.length === 1 ? "file" : "files"}: ${added} new ${added === 1 ? "activity" : "activities"}${nights.size ? `, ${nights.size} ${nights.size === 1 ? "night" : "nights"} of HRV, resting HR and sleep` : ""}.`);
      const imported = [
        added ? `${added} new ${added === 1 ? "activity" : "activities"}` : "",
        nights.size ? `${nights.size} ${nights.size === 1 ? "night" : "nights"} of health data` : "",
      ].filter(Boolean).join(" and ");
      setMessage({ ok: !failed, text: failed
        ? `Sync incomplete. ${imported ? `${imported} added; ` : ""}${failed} files couldn't import. Try again to finish.`
        : imported ? `Sync complete. ${imported} added.` : "Your watch is up to date. No new data." });
      await done(added, failed ? `${failed} of ${todo.length} files failed.` : null);
    } catch (e) {
      const msg = (e as Error).message;
      if ((e as Error).name !== "NotFoundError") await done(added, msg);
      // Chrome on a Mac refuses to connect to Garmin watches; Windows, Android and the iPhone app work.
      if (/unsupported device/i.test(msg)) say(false, "This browser can't connect to the watch (Chrome on a Mac refuses it). Sync from the Talus iPhone app instead, or Chrome on Windows or Android.");
      else say(false, (e as Error).name === "NotFoundError"
        ? "No watch chosen. Try again and select your watch. If it isn't listed, follow the connection steps above." : msg);
      setMessage({ ok: false, text: `${out.at(-1)?.text ?? msg}${added ? ` ${added} new activities were saved.` : ""}` });
    } finally {
      session.disconnect();
      setProgress(null);
      setBusy(false);
      router.refresh();
    }
  }

  // On, the iPhone app syncs the watch on its own whenever Talus opens, and this card can too.
  async function turn(enabled: boolean) {
    setSwitching(true);
    setSwitchError(null);
    const res = await fetch("/api/sources/watch", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled }),
    }).catch(() => null);
    setSwitching(false);
    if (!res?.ok) return setSwitchError(res ? `Could not save (${res.status}).` : "Could not reach the engine. Is it running?");
    router.refresh();
  }

  const p = progress;
  return (
    <section className="card" id="garmin-watch" aria-labelledby="watch-title">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="label" id="watch-title">Garmin watch</h2>
        <div className="actions" style={{ gap: 6 }}>
          <Pill tone="warn">Experimental</Pill>
          {!watch.enabled ? <Pill tone="neutral">Off</Pill> : watch.last_attempt?.error ? <Pill tone="warn">Needs attention</Pill> : <Pill tone="neutral">On</Pill>}
        </div>
      </div>
      <div className="muted">
        Import activities directly over Bluetooth. Activities already in Talus are skipped.
      </div>
      {!watch.enabled ? (
        <div className="actions">
          <button className="btn light outline" onClick={() => turn(true)} disabled={switching} aria-busy={switching}>Turn on</button>
        </div>
      ) : where === null ? <div className="muted">Checking Bluetooth support…</div> : where === "ok" || tryHere ? (
        <>
          <ol className="muted" style={{ margin: 0, paddingLeft: 20 }}>
            <li>Keep your watch close to this device.</li>
            <li>Turn off Bluetooth in your phone&apos;s Settings so this browser can reach the watch.</li>
            <li>Tap Sync from watch, then choose your watch.</li>
          </ol>
          <div className="actions">
            <button className="btn" onClick={sync} disabled={busy || switching} aria-busy={busy}>{busy ? "Syncing…" : "Sync from watch"}</button>
          </div>
        </>
      ) : (
        <div>
          <strong>Sync from the Talus iPhone app.</strong>{" "}
          <span className="muted">
            {where === "mac" ? "Chrome on a Mac can't connect to Garmin watches. " : "This browser can't use Bluetooth. "}
            Open Talus on your iPhone, then go to Settings → Data sources → Sync now. Keep the watch nearby
            and the app open. Chrome or Edge on Windows or Android can also sync from here.
          </span>
          {where === "mac" ? (
            <div className="actions"><button className="link-btn" onClick={() => setTryHere(true)}>Try this browser anyway</button></div>
          ) : null}
        </div>
      )}
      <div role="status" aria-live="polite" aria-atomic="true">
        {busy ? <div className="sync-feedback"><strong>{p?.phase ?? "Connecting to your watch…"}</strong>
          {p?.total ? <span className="muted">File {(p.done ?? 0) + 1} of {p.total}</span> : null}
          <span className="muted">Keep the watch nearby and this page open.</span>
        </div> : message ? <div className={message.ok ? "ok-text" : "error-text"}>{message.text}</div> : null}
      </div>
      {!busy && lines.length ? <details className="sync-options"><summary>Sync details</summary>
        {lines.map((l, i) => <div key={i} className={l.ok ? "muted" : "error-text"}>{l.text}</div>)}
      </details> : null}
      {added.length ? (
        <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
          {added.map((a) => <li key={a.id}><a href={`/activities/${a.id}`}>{addedLine(a)}</a></li>)}
        </ul>
      ) : null}
      {watch.enabled && !lines.length && lastSync ? <div className="muted">Last sync {when(lastSync)}.</div> : null}
      {watch.enabled && !lastSync && !busy ? <div className="muted">The first sync brings in the last {FIRST_SYNC_DAYS} days of activities, about half a minute each.</div> : null}
      <details className="sync-options"><summary>What syncs from the watch?</summary><div className="muted">
        Activities sync over Bluetooth; the iPhone app also checks automatically when opened, at most hourly.
        Available sleep, HRV and resting heart rate files are also imported. Intervals.icu or Apple Health can supply data the watch no longer has. Nothing on the watch is changed or deleted.
      </div></details>
      {switchError ? <div className="error-text">{switchError}</div> : null}
      {watch.enabled ? (
        <div className="actions">
          <button className="link-btn" style={{ fontSize: 13 }} onClick={() => turn(false)} disabled={switching || busy}>Turn off</button>
        </div>
      ) : null}
    </section>
  );
}
