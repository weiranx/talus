"""Activity names written by the coach: the peak or place and the kind of day, like "Mt Baldy
via the Bowl" or "Easy loop at Griffith Park".

A watch or intervals.icu gives most activities a generic name ("Pasadena Trail Running",
"Morning Run"). Those get the coach's name; a name that looks typed by the athlete is kept, and a
name typed in Talus always wins. Place names come from OpenStreetMap: the nearest named peak to
the high point (Overpass) and the area around the start (Nominatim), cached by rounded position.
Only those names go to the coach, never coordinates.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time

import httpx

USER_AGENT = "Talus/1.0 (mountain training app; https://talus.weiranxiong.com)"
NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
OVERPASS = "https://overpass-api.de/api/interpreter"
PEAK_RADIUS_M = 400
NAME_MAX = 60

# Names a watch or platform makes up: an activity type, maybe after a time of day ("Morning Run",
# "Evening Trail Run"), or the type's full word after a place ("Pasadena Trail Running",
# "Los Angeles County Hiking"). A place before a short form ("Mt Whitney Hike", "Baldy Bowl run")
# reads as typed by the athlete, and so do names like "Tempo run" or "Long run".
_FULL = (r"(?:(?:trail|treadmill|indoor|street|track|virtual|ultra)\s+)?(?:running|hiking|walking|cycling|biking|"
         r"mountaineering|climbing|(?:(?:backcountry|cross[- ]country)\s+)?skiing|ski\s+touring|snowshoeing|cardio)")
_SHORT = (r"(?:(?:(?:trail|treadmill|indoor|street|track|virtual)\s+)?(?:run|walk|ride)|hike|ski|climb|snowshoe|"
          r"activity|workout|other)")
_WHEN = r"(?:(?:morning|afternoon|evening|night|lunch|early|late)\s+)"
GENERIC = re.compile(rf"^\s*(?:{_WHEN}?(?:{_FULL}|{_SHORT})|(?:[\w.'’,-]+\s+)+{_FULL})\s*$", re.IGNORECASE)
FILE_NAME = re.compile(r"\.(?:fit|gpx|tcx)$", re.IGNORECASE)
# A file's stem or a device's id: digits, maybe after a sport or app word, like "run01" or
# "2026-10-11-0715". A race name with digits ("WS100", "UTMB2025") is the athlete's.
FILE_STEM = re.compile(r"^(?:(?:run|hike|walk|ride|activity|track|workout|export|garmin|coros|strava|file)[_-]?)?"
                       r"[\d_-]*\d{2,}[\d_-]*$", re.IGNORECASE)


def is_generic(name: str | None) -> bool:
    """True when the name came from a device or platform, not from the athlete."""
    if not name or not name.strip():
        return True
    return bool(GENERIC.match(name) or FILE_NAME.search(name.strip()) or FILE_STEM.match(name.strip()))


def enabled() -> bool:
    return os.environ.get("TALUS_GEOCODE", "on").lower() not in ("off", "0", "false", "no")


# Places ------------------------------------------------------------------------------------

_nominatim_guard = threading.Lock()
_nominatim_last = 0.0


class LookupFailed(Exception):
    """The lookup didn't get an answer (offline, timed out, rate limited): ask again next time."""


def _get(url: str, params: dict, timeout: float = 8.0) -> dict | list:
    try:
        r = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise LookupFailed(str(e)) from e


def area_name(lat: float, lon: float) -> str | None:
    """The park, trail area or town around a point. Nominatim allows one request a second."""
    global _nominatim_last
    with _nominatim_guard:
        wait = 1.1 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            time.sleep(wait)
        data = _get(NOMINATIM, {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "format": "jsonv2", "zoom": 14,
                                "accept-language": "en"})
        _nominatim_last = time.monotonic()
    if not isinstance(data, dict):
        return None
    addr = data.get("address") or {}
    for key in ("leisure", "park", "nature_reserve", "protected_area", "national_park", "forest", "natural",
                "hamlet", "village", "suburb", "town", "city"):
        if addr.get(key):
            return str(addr[key])[:80]
    return (data.get("name") or None) and str(data["name"])[:80]


def peak_name(lat: float, lon: float) -> str | None:
    """The nearest named peak within PEAK_RADIUS_M of a point."""
    q = (f"[out:json][timeout:10];node(around:{PEAK_RADIUS_M},{lat:.5f},{lon:.5f})"
         "[natural~\"^(peak|volcano|saddle)$\"][name];out 5;")
    data = _get(OVERPASS, {"data": q}, timeout=12.0)
    if not isinstance(data, dict):
        return None
    if "error" in str(data.get("remark") or "").lower():  # Overpass answers 200 with a remark when its query times out
        raise LookupFailed(str(data["remark"]))
    best = None
    for el in data.get("elements") or []:
        if el.get("tags", {}).get("natural") == "saddle":
            continue
        d = _meters(lat, lon, el["lat"], el["lon"])
        if best is None or d < best[0]:
            best = (d, el["tags"]["name"])
    return None if best is None else str(best[1])[:80]


def _meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    k = math.pi / 180
    x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
    return math.hypot(x, (lat2 - lat1) * k) * 6_371_000


def places(store, lat: float | None, lon: float | None, high: tuple[float, float] | None) -> dict:
    """The area at the start and the peak at the high point, cached by position (about 100 m)."""
    out = {"area": None, "peak": None}
    if not enabled():
        return out
    if lat is not None and lon is not None:
        out["area"] = _cached(store, f"area:{lat:.3f},{lon:.3f}", lambda: area_name(lat, lon))
    if high is not None:
        out["peak"] = _cached(store, f"peak:{high[0]:.3f},{high[1]:.3f}", lambda: peak_name(*high))
    return out


def _cached(store, key: str, fetch) -> str | None:
    hit = store.place(key)
    if hit is not None:
        return hit or None
    try:
        value = fetch()
    except LookupFailed:
        return None  # not cached, so the next activity here asks again
    store.save_place(key, value or "")  # an empty answer is cached too, so nothing is asked twice
    return value


# What the coach reads ----------------------------------------------------------------------

def features(detail: dict) -> tuple[dict, tuple | None, tuple | None]:
    """The shape of the route from an activity's detail payload: where it starts, its high point,
    and whether it's a loop, an out-and-back or point to point."""
    s = detail.get("streams") or {}
    lat, lon, alt = s.get("lat"), s.get("lon"), s.get("altitude_ft")
    pts = [(a, b) for a, b in zip(lat or [], lon or []) if a is not None and b is not None]
    start = pts[0] if pts else None
    high = None
    if pts and alt and len(alt) == len(lat or []):
        i = max((k for k in range(len(alt)) if alt[k] is not None and lat[k] is not None), key=lambda k: alt[k],
                default=None)
        high = None if i is None else (lat[i], lon[i])
    shape = None
    if len(pts) > 10:
        far = max(_meters(*start, *p) for p in pts)
        end = _meters(*start, *pts[-1])
        if far < 200:
            shape = "in one place (track or treadmill)"
        elif end > 0.3 * far:
            shape = "point to point"
        else:
            # Out-and-back retraces itself: the way back passes close to the way out.
            half = pts[: len(pts) // 2]
            back = pts[len(pts) // 2:][::max(1, len(pts) // 40)]
            near = sum(1 for p in back if min(_meters(*p, *q) for q in half[::max(1, len(half) // 200)]) < 60)
            shape = "out and back" if near > 0.7 * len(back) else "loop"
    return {"shape": shape}, start, high


def packet(row, detail: dict, place: dict, shape: dict) -> dict:
    zm = detail.get("zone_minutes") or {}
    total = sum(zm.values()) if zm else 0
    return {
        "sport": row["sport"] or detail.get("sport"),
        "name_from_device": row["name"],
        "distance_mi": round(detail["distance_mi"], 1), "gain_ft": round(detail["gain_ft"]),
        "loss_ft": round(detail["loss_ft"]), "hike_share_pct": round(detail["hike_share"] * 100),
        "longest_climb_ft": round(detail.get("longest_climb_ft") or 0),
        "below_aet_pct": None if not total else round(zm.get("z1", 0) / total * 100),
        "above_ant_pct": None if not total else round(zm.get("z3", 0) / total * 100),
        "route_shape": shape["shape"], "peak_near_high_point": place["peak"], "area_at_start": place["area"],
    }


def rules_name(p: dict) -> dict:
    """A name without the coach: the peak or area and the kind of day."""
    sport = (p["sport"] or "").lower()
    kind = "Hike" if "hik" in sport or p["hike_share_pct"] >= 60 else "Run"
    if p["peak_near_high_point"]:
        return {"name": f"{p['peak_near_high_point']} {kind.lower()}"[:NAME_MAX]}
    if p["area_at_start"]:
        return {"name": f"{kind} at {p['area_at_start']}"[:NAME_MAX]}
    return {"name": (p["name_from_device"] or kind)[:NAME_MAX]}


def clean(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("*", "").strip().strip("\"'“”")).rstrip(".")[:NAME_MAX]

