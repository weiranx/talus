"""Turn a raw activity file into per-second streams."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import fitdecode
import numpy as np

from .models import Streams
from .sources.base import RawFile

SEMICIRCLE = 180.0 / 2**31


def parse(raw: RawFile) -> Streams:
    parsers = {"fit": parse_fit, "gpx": parse_gpx, "tcx": parse_tcx, "icu-streams": parse_icu_streams}
    if raw.fmt not in parsers:
        raise ValueError(f"No parser for {raw.fmt}")
    return finish(parsers[raw.fmt](raw.data))


def parse_fit(data: bytes) -> Streams:
    rows: list[dict] = []
    sport = None
    activity = {}
    with fitdecode.FitReader(io.BytesIO(data)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                rows.append({f.name: f.value for f in frame.fields if f.value is not None})
            elif frame.name == "session" and frame.has_field("sport"):
                sport = str(frame.get_value("sport"))
            elif frame.name == "activity":
                activity = {f.name: f.value for f in frame.fields if f.value is not None}
    rows = [r for r in rows if "timestamp" in r]
    if not rows:
        raise ValueError("FIT file has no records")

    def col(*names, scale=1.0):
        vals = []
        for r in rows:
            v = next((r[n] for n in names if n in r), None)
            vals.append(np.nan if v is None else float(v) * scale)
        arr = np.array(vals)
        return None if np.all(np.isnan(arr)) else arr

    t0 = rows[0]["timestamp"]
    cadence = col("cadence")
    if cadence is not None:
        frac = col("fractional_cadence")
        if frac is not None:
            cadence = cadence + np.nan_to_num(frac)
        cadence = cadence * 2  # FIT running cadence is per foot
    return Streams(
        time=np.array([(r["timestamp"] - t0).total_seconds() for r in rows]),
        distance=col("distance"),
        altitude=col("enhanced_altitude", "altitude"),
        heart_rate=col("heart_rate"),
        cadence=cadence,
        lat=col("position_lat", scale=SEMICIRCLE),
        lon=col("position_long", scale=SEMICIRCLE),
        start_time=t0,
        start_local=_fit_local_start(t0, activity),
        sport=sport,
        barometric=True,  # assumption: most GPS watches record barometric altitude in FIT
    )


FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)


def _fit_local_start(t0: datetime, activity: dict) -> datetime | None:
    """The watch's wall-clock start: the activity message's local_timestamp minus its UTC
    timestamp gives the zone offset, rounded to 15 minutes."""
    utc, local = activity.get("timestamp"), activity.get("local_timestamp")
    if utc is None or local is None:
        return None
    if isinstance(local, (int, float)):
        local = FIT_EPOCH + timedelta(seconds=local)
    if isinstance(utc, (int, float)):
        utc = FIT_EPOCH + timedelta(seconds=utc)
    local, utc = local.replace(tzinfo=None), utc.astimezone(timezone.utc).replace(tzinfo=None)
    offset = timedelta(minutes=round((local - utc).total_seconds() / 900) * 15)
    if abs(offset) > timedelta(hours=14):
        return None
    return (t0.astimezone(timezone.utc).replace(tzinfo=None) if t0.tzinfo else t0) + offset


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el.iter():
        if _local(child.tag) == name:
            return child
    return None


def _num(el: ET.Element | None) -> float:
    try:
        return float(el.text) if el is not None and el.text else np.nan
    except ValueError:
        return np.nan


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def parse_gpx(data: bytes) -> Streams:
    root = ET.fromstring(data)
    pts = [el for el in root.iter() if _local(el.tag) == "trkpt"]
    times, lat, lon, ele, hr, cad = [], [], [], [], [], []
    for p in pts:
        t = _find(p, "time")
        if t is None:
            continue
        times.append(_time(t.text))
        lat.append(float(p.get("lat")))
        lon.append(float(p.get("lon")))
        ele.append(_num(_find(p, "ele")))
        hr.append(_num(_find(p, "hr")))
        cad.append(_num(_find(p, "cad")))
    if not times:
        raise ValueError("GPX file has no timed track points")
    cadence = _opt(cad)
    return Streams(
        time=np.array([(t - times[0]).total_seconds() for t in times]),
        altitude=_opt(ele), heart_rate=_opt(hr),
        cadence=None if cadence is None else cadence * 2,  # Garmin GPX cadence is per foot
        lat=np.array(lat), lon=np.array(lon), start_time=times[0],
    )


def parse_tcx(data: bytes) -> Streams:
    root = ET.fromstring(data)
    pts = [el for el in root.iter() if _local(el.tag) == "Trackpoint"]
    times, lat, lon, ele, dist, hr, cad = [], [], [], [], [], [], []
    sport = None
    act = _find(root, "Activity")
    if act is not None:
        sport = act.get("Sport")
    for p in pts:
        t = _find(p, "Time")
        if t is None:
            continue
        times.append(_time(t.text))
        lat.append(_num(_find(p, "LatitudeDegrees")))
        lon.append(_num(_find(p, "LongitudeDegrees")))
        ele.append(_num(_find(p, "AltitudeMeters")))
        dist.append(_num(_find(p, "DistanceMeters")))
        hr_el = _find(p, "HeartRateBpm")
        hr.append(_num(_find(hr_el, "Value")) if hr_el is not None else np.nan)
        c = _find(p, "RunCadence")
        cad.append(_num(c if c is not None else _find(p, "Cadence")))
    if not times:
        raise ValueError("TCX file has no timed track points")
    cadence = _opt(cad)
    return Streams(
        time=np.array([(t - times[0]).total_seconds() for t in times]),
        distance=_opt(dist), altitude=_opt(ele), heart_rate=_opt(hr),
        cadence=None if cadence is None else cadence * 2,
        lat=_opt(lat), lon=_opt(lon), start_time=times[0], sport=sport,
    )


def parse_icu_streams(data: bytes) -> Streams:
    by_type = {s["type"]: s for s in json.loads(data)}
    if "time" not in by_type:
        raise ValueError("intervals.icu streams have no time channel")

    def get(name, key="data"):
        s = by_type.get(name)
        return _opt(s[key]) if s and s.get(key) else None

    cadence = get("cadence")
    if cadence is not None:
        moving = cadence[cadence > 0]
        # Assumption: values under 110 are per-foot cadence and need doubling.
        if moving.size and np.median(moving) < 110:
            cadence = cadence * 2
    return Streams(
        time=np.array(by_type["time"]["data"], dtype=float),
        distance=get("distance"), altitude=get("altitude"), heart_rate=get("heartrate"),
        cadence=cadence, lat=get("latlng"), lon=get("latlng", "data2"),
    )


def _opt(values) -> np.ndarray | None:
    arr = np.array([np.nan if v is None else v for v in values], dtype=float)
    return None if arr.size == 0 or np.all(np.isnan(arr)) else arr


def haversine_cumulative(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    r = 6371008.8
    la, lo = np.radians(lat), np.radians(lon)
    dla, dlo = np.diff(la), np.diff(lo)
    a = np.sin(dla / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(dlo / 2) ** 2
    step = 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.concatenate([[0.0], np.cumsum(np.nan_to_num(step))])


def _fill(arr: np.ndarray | None) -> np.ndarray | None:
    """Linear-interpolate gaps; hold the edges."""
    if arr is None:
        return None
    ok = ~np.isnan(arr)
    if ok.all() or not ok.any():
        return arr
    idx = np.arange(arr.size)
    return np.interp(idx, idx[ok], arr[ok])


def finish(s: Streams) -> Streams:
    """Sort, de-duplicate timestamps, fill gaps and derive distance when the file lacks it."""
    order = np.argsort(s.time, kind="stable")
    keep = np.concatenate([[True], np.diff(s.time[order]) > 0])
    idx = order[keep]
    for name in ("time", "distance", "altitude", "heart_rate", "cadence", "lat", "lon"):
        arr = getattr(s, name)
        if arr is not None:
            setattr(s, name, arr[idx])
    s.lat, s.lon = _fill(s.lat), _fill(s.lon)
    if s.distance is None or np.all(np.isnan(s.distance)):
        if s.lat is None or s.lon is None:
            raise ValueError("Activity has neither distance nor GPS")
        s.distance = haversine_cumulative(s.lat, s.lon)
    s.distance = np.maximum.accumulate(_fill(s.distance))
    s.altitude = _fill(s.altitude)
    if s.heart_rate is not None:
        hr = s.heart_rate.copy()
        hr[(hr < 30) | (hr > 230)] = np.nan  # dropouts and spikes
        s.heart_rate = None if np.isnan(hr).mean() > 0.5 else _fill(hr)
    s.cadence = _fill(s.cadence)
    return s
