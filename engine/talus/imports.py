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
from .sources.base import RawFile, RemoteActivity, local_naive, sniff_format
from .store import Store

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
        try:
            raw = RawFile(data, sniff_format(data))
            streams = parse(raw)
        except Exception as e:
            result["failed"].append(f"{name}: {e}")
            log(f"  skip {name}: {e}")
            return
        start = local_naive(streams.start_time) if streams.start_time else None
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
        log(f"{result['listed']} activities read, {result['new']} new, {result['duplicates']} already here from another source")
        if added and th is not None:
            rebuild_ledgers(store)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        store.save_settings({"last_import": result})
    return result | {"added_ids": added}
