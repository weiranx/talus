"""File imports: FIT, GPX and TCX files, gzipped or not, zips of them, and Apple Health exports.

Used by `talus import` and by the web app's upload. Each activity is checked against what is
already stored, so a file you also synced from intervals.icu or Garmin is not counted twice.
"""

from __future__ import annotations

import gzip
import hashlib
import zipfile
from datetime import date, datetime
from pathlib import Path

from .parse import parse
from .sources import apple_health
from .sources.base import RawFile, RemoteActivity, sniff_format, wall_clock_start
from .store import Store
from .watch import WatchWellness, fit_file_type

ACTIVITY_EXT = (".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz")


def import_paths(store: Store, paths: list[tuple[str, Path]], log=print, since: date | None = None) -> dict:
    """Import uploaded or local files: (display name, path on disk) pairs. Scores what is new and
    rebuilds the ledgers. The outcome is kept as the last import for the Settings screen."""
    from .cli import _score_and_save, rebuild_ledgers

    result = {"at": datetime.now().isoformat(timespec="seconds"), "source": "file", "files": len(paths),
              "listed": 0, "new": 0, "saved": 0, "duplicates": 0, "failed": [], "wellness_days": 0,
              "error": None}
    added: list[int] = []
    th = store.thresholds()
    watch = WatchWellness()

    def add(act: RemoteActivity, raw: RawFile, label: str) -> None:
        result["listed"] += 1
        if store.has(act.source, act.source_id):
            return
        if store.duplicate_of(act.start_time, act.distance_m) is not None:
            result["duplicates"] += 1
            return
        result["new"] += 1
        activity_id = store.add(act, raw)
        added.append(activity_id)
        result["saved"] += 1
        _score_and_save(store, activity_id, raw, th, act.sport, label=label, log=log)

    def one_file(name: str, data: bytes) -> None:
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        if data[8:12] == b".FIT" and fit_file_type(data) not in (None, "activity"):
            # A watch's own folder: overnight files become wellness, the rest (settings, totals,
            # courses) are not Talus's to read.
            if not watch.add(data):
                result["other_files"] = result.get("other_files", 0) + 1
            return
        try:
            raw = RawFile(data, sniff_format(data))
            streams = parse(raw)
        except Exception as e:
            result["failed"].append(f"{name}: {e}")
            log(f"  skip {name}: {e}")
            return
        start = wall_clock_start(streams)
        act = RemoteActivity(source="file", source_id=hashlib.sha256(data).hexdigest()[:16], start_time=start,
                             name=Path(name).name.split(".")[0], sport=streams.sport,
                             distance_m=float(streams.distance[-1]) if streams.distance is not None else None)
        add(act, raw, name)

    try:
        for name, path in paths:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    if apple_health.is_export(zf):
                        result["source"] = apple_health.SOURCE
                        log(f"{name}: Apple Health export")
                        export = apple_health.read_export(path, since)
                        for act, raw in export.activities:
                            add(act, raw, f"{act.start_time:%Y-%m-%d} {act.name}")
                        store.save_wellness(export.wellness, fill_only=True)
                        result["wellness_days"] += len(export.wellness)
                        result["no_data"] = result.get("no_data", 0) + export.skipped
                        continue
                    members = [m for m in zf.namelist() if m.lower().endswith(ACTIVITY_EXT)]
                    if not members:
                        result["failed"].append(f"{name}: no FIT, GPX or TCX files inside")
                    for m in members:
                        one_file(m, zf.read(m))
            else:
                one_file(name, path.read_bytes())
        nights = watch.days()
        if nights:
            store.save_wellness(merge_wellness(store, nights))
            result["source"] = "watch" if not result["listed"] else result["source"]
            result["wellness_days"] += len(nights)
            # The mornings filled, so an app sending a watch's files one at a time can count
            # nights once when a night's sleep and HRV files arrive separately.
            result["wellness_dates"] = sorted({n.day.isoformat() for n in nights})
            log(f"{len(nights)} days of overnight HRV, resting HR and sleep from the watch's files")
        log(f"{result['listed']} activities read, {result['new']} new, {result['duplicates']} already here from another source")
        if added and th is not None:
            rebuild_ledgers(store)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        store.save_settings({"last_import": result})
    return result | {"added_ids": added}


def merge_wellness(store: Store, days: list) -> list:
    """Fill what the watch's files lack (a day with sleep but no HRV file) from what is stored, so
    a partial copy of the watch never blanks a fuller night."""
    stored = store.wellness(min(d.day for d in days), max(d.day for d in days))
    for d in days:
        old = stored.get(d.day) or {}
        for k in ("hrv", "resting_hr", "sleep_s", "sleep_score"):
            if getattr(d, k) is None and old.get(k) is not None:
                setattr(d, k, old[k])
    return days


HEALTHKIT = "healthkit"
HEALTHKIT_SPORTS = {"run": "running", "trail_run": "trail_running", "hike": "hiking", "walk": "walking",
                    "ride": "cycling", "other": "other"}


def import_healthkit(store: Store, workouts: list[dict], wellness: list[dict], log=print) -> dict:
    """Workouts and nights posted by the Talus iOS app from HealthKit. Each workout carries a TCX
    document, so it scores like any watch file; its HealthKit UUID keeps re-uploads from adding it
    twice, and the same run from another source is kept once. HRV is not stored: HealthKit's is
    SDNN, not the overnight rMSSD the readiness baseline is built on."""
    from .cli import _score_and_save, rebuild_ledgers
    from .sources.base import WellnessDay

    th = store.thresholds()
    result = {"at": datetime.now().isoformat(timespec="seconds"), "source": HEALTHKIT,
              "workouts_added": 0, "workouts_skipped": 0, "wellness_days": 0, "failed": [], "error": None}
    added: list[int] = []
    for w in workouts:
        label = f"{w['start'][:10]} {w.get('name') or w['sport']}"
        raw = RawFile(w["tcx"].encode(), "tcx")
        try:
            streams = parse(raw)
        except Exception as e:
            result["failed"].append(f"{w['uuid']}: {e}")
            result["workouts_skipped"] += 1
            log(f"  skip {label}: {e}")
            continue
        start = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
        # The wall-clock time where it happened, like intervals.icu's and Garmin's local start.
        start = start.replace(tzinfo=None) if start.tzinfo else start
        sport = HEALTHKIT_SPORTS.get(w["sport"], w["sport"])
        distance = float(streams.distance[-1]) if streams.distance is not None else None
        act = RemoteActivity(source=HEALTHKIT, source_id=w["uuid"], start_time=start,
                             name=w.get("name") or sport.replace("_", " ").capitalize(), sport=sport,
                             distance_m=distance, device_name=w.get("device_name"))
        if store.has(HEALTHKIT, w["uuid"]) or store.duplicate_of(start, distance) is not None:
            result["workouts_skipped"] += 1
            continue
        activity_id = store.add(act, raw)
        added.append(activity_id)
        result["workouts_added"] += 1
        _score_and_save(store, activity_id, raw, th, sport, label=label, log=log)
    days = [WellnessDay(source=HEALTHKIT, day=date.fromisoformat(d["day"]), resting_hr=d.get("resting_hr"),
                        sleep_s=d.get("sleep_s")) for d in wellness]
    days = [d for d in days if d.resting_hr is not None or d.sleep_s is not None]
    # A watch platform's night (with rMSSD HRV) is never replaced; a later intervals.icu or
    # Garmin sync replaces these.
    store.save_wellness(days, fill_only=True)
    result["wellness_days"] = len(days)
    if added and th is not None:
        rebuild_ledgers(store)
    store.save_settings({"last_sync_healthkit": result})
    return result | {"added_ids": added}
