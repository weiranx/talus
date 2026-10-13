"""Every route the athlete has recorded, for the heatmap: one simplified line per activity.

Reading a FIT file takes about a second, so each line is worked out once and kept in the
account's cache/tracks.json, keyed on the file it came from. Most come from the activity page's
own cache (the background read-ahead has usually done every activity already), which holds the
track at 800 points; the rest are read from their files in a background thread.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np

VERSION = 1
TOLERANCE_M = 6.0  # a point closer than this to the line through its neighbours is dropped
MAX_POINTS = 800  # the same thinning as the activity page's map

# Sports grouped the way the heatmap filters them. Sources spell them differently (Garmin
# "trail_running", intervals.icu "TrailRun", Apple Health "Running").
KINDS = {
    "trail": {"trail_running", "trailrun", "trail_run", "mountaineering", "skimountaineering", "backcountryski",
              "backcountry_skiing", "ski_touring"},
    "run": {"running", "run", "virtualrun", "treadmill_running", "track_running", "street_running"},
    "hike": {"hiking", "hike", "walking", "walk", "snowshoe", "snowshoeing"},
    "ride": {"cycling", "ride", "virtualride", "mountainbikeride", "gravelride", "ebikeride", "emountainbikeride",
             "road_biking", "mountain_biking", "gravel_cycling", "biking"},
}


def kind(sport: str | None) -> str:
    s = (sport or "").lower().replace(" ", "_")
    for k, names in KINDS.items():
        if s in names or s.replace("_", "") in names:
            return k
    return "other"


def simplify(lat: np.ndarray, lon: np.ndarray, tol_m: float = TOLERANCE_M) -> list[tuple[float, float]]:
    """Douglas-Peucker on a local flat projection: the fewest points that stay within tol_m of the track."""
    ok = np.isfinite(lat) & np.isfinite(lon) & ~((np.abs(lat) < 1e-6) & (np.abs(lon) < 1e-6))
    lat, lon = lat[ok], lon[ok]
    if len(lat) < 2:
        return []
    y = (lat - lat[0]) * 111_320.0
    x = (lon - lon[0]) * 111_320.0 * np.cos(np.radians(lat[0]))
    keep = np.zeros(len(lat), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(lat) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        px, py = x[a + 1:b] - x[a], y[a + 1:b] - y[a]
        seg = np.hypot(dx, dy)
        d = np.abs(px * dy - py * dx) / seg if seg > 0 else np.hypot(px, py)
        i = int(np.argmax(d))
        if d[i] > tol_m:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return [(float(la), float(lo)) for la, lo in zip(lat[keep], lon[keep])]


def encode(points: list[tuple[float, float]]) -> str:
    """Google's encoded polyline (5 decimals, about a metre): a few bytes a point instead of ~20 in JSON."""
    out, plat, plon = [], 0, 0
    for la, lo in points:
        ilat, ilon = round(la * 1e5), round(lo * 1e5)
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def decode(line: str) -> list[tuple[float, float]]:
    pts, i, la, lo = [], 0, 0, 0
    while i < len(line):
        vals = []
        for _ in range(2):
            shift = res = 0
            while True:
                b = ord(line[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(res >> 1) if res & 1 else res >> 1)
        la += vals[0]
        lo += vals[1]
        pts.append((la / 1e5, lo / 1e5))
    return pts


def line_from(lat, lon) -> str | None:
    """The stored line for a track, or None for one without GPS (a treadmill run)."""
    if lat is None or lon is None:
        return None
    lat = np.asarray([np.nan if v is None else v for v in lat], dtype=float)
    lon = np.asarray([np.nan if v is None else v for v in lon], dtype=float)
    if len(lat) > MAX_POINTS:
        idx = np.arange(0, len(lat), len(lat) // MAX_POINTS)
        lat, lon = lat[idx], lon[idx]
    pts = simplify(lat, lon)
    return encode(pts) if len(pts) >= 2 else None


class Tracks:
    """An account's cache/tracks.json: {activity id: {"f": file key, "p": line or null}}."""

    _guards: dict[str, threading.Lock] = {}
    _building: set[str] = set()
    _meta = threading.Lock()

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.path = self.dir / "cache" / "tracks.json"
        with Tracks._meta:
            self.guard = Tracks._guards.setdefault(str(self.dir), threading.Lock())

    def load(self) -> dict[str, dict]:
        try:
            body = json.loads(self.path.read_text())
            return body["tracks"] if body.get("v") == VERSION else {}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def save(self, tracks: dict[str, dict]) -> None:
        try:
            self.path.parent.mkdir(exist_ok=True)
            tmp = self.path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"v": VERSION, "tracks": tracks}, separators=(",", ":")))
            tmp.replace(self.path)
        except OSError:
            pass  # a full or read-only disk only costs the next view a re-read

    def file_key(self, row) -> list | None:
        try:
            st = (self.dir / row["raw_path"]).stat()
        except OSError:
            return None
        return [row["raw_path"], st.st_mtime_ns, st.st_size]

    def from_detail(self, row, key: list) -> str | None | bool:
        """The line from the activity page's cached detail, if one newer than the file is there; False if none."""
        for p in sorted((self.dir / "cache").glob(f"activity-{row['id']}-*.json")):
            try:
                if p.stat().st_mtime_ns < key[1]:
                    continue
                s = json.loads(p.read_text())["streams"]
                return line_from(s.get("lat"), s.get("lon"))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return False

    def lines(self, rows) -> tuple[dict[int, str | None], list]:
        """Each activity's line, from the cache or the activity page's cache. Returns the lines
        found and the rows still to read from their files."""
        with self.guard:
            tracks = self.load()
            changed, out, missing = False, {}, []
            for row in rows:
                key = self.file_key(row)
                if key is None:
                    out[row["id"]] = None
                    continue
                hit = tracks.get(str(row["id"]))
                if hit and hit.get("f") == key:
                    out[row["id"]] = hit.get("p")
                    continue
                line = self.from_detail(row, key)
                if line is False:
                    missing.append(row)
                    continue
                tracks[str(row["id"])] = {"f": key, "p": line}
                out[row["id"]] = line
                changed = True
            ids = {str(r["id"]) for r in rows}
            if any(k not in ids for k in tracks):  # deleted activities
                tracks = {k: v for k, v in tracks.items() if k in ids}
                changed = True
            if changed:
                self.save(tracks)
        return out, missing

    def put(self, row, line: str | None) -> None:
        key = self.file_key(row)
        if key is None:
            return
        with self.guard:
            tracks = self.load()
            tracks[str(row["id"])] = {"f": key, "p": line}
            self.save(tracks)

    def build(self, rows, read) -> None:
        """Read the missing tracks from their files in a low-priority background thread.
        read(row) returns the parsed streams."""
        key = str(self.dir)
        with Tracks._meta:
            if key in Tracks._building or not rows:
                return
            Tracks._building.add(key)

        def run() -> None:
            try:
                try:  # the server's requests come first
                    os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
                except (AttributeError, OSError):
                    pass
                for row in rows:
                    started = time.monotonic()
                    try:
                        s = read(row)
                        self.put(row, line_from(s.lat, s.lon))
                    except Exception:  # an unreadable file: no line, and its page says why
                        self.put(row, None)
                    time.sleep(max(0.05, time.monotonic() - started))
            finally:
                with Tracks._meta:
                    Tracks._building.discard(key)

        threading.Thread(target=run, name=f"tracks {key}", daemon=True).start()

    def building(self) -> bool:
        with Tracks._meta:
            return str(self.dir) in Tracks._building
