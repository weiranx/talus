"""Apple Health export: the export.zip from the Health app (profile picture > Export All Health Data).

Workouts come out as activities. A workout with a route gets that route's GPX with the heart
rate samples recorded during it merged in, so it scores like a watch file; one without a route
(treadmill, gym) gets a heart-rate-only track with its total distance spread evenly.

Overnight data: resting heart rate and sleep fill days no other source has. Apple's HRV is SDNN,
not the overnight rMSSD every other source reports, so it is left out rather than mixed into one
baseline.

export.xml can be gigabytes, so it is streamed twice: once for workouts and daily records, once
for the heart rate samples inside those workouts.
"""

from __future__ import annotations

import bisect
import hashlib
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import iterparse
from xml.sax.saxutils import escape

from ..parse import _find, _local, _time
from .base import RawFile, RemoteActivity, WellnessDay

SOURCE = "apple_health"
HR = "HKQuantityTypeIdentifierHeartRate"
RESTING_HR = "HKQuantityTypeIdentifierRestingHeartRate"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
ASLEEP = {"HKCategoryValueSleepAnalysisAsleep", "HKCategoryValueSleepAnalysisAsleepUnspecified",
          "HKCategoryValueSleepAnalysisAsleepCore", "HKCategoryValueSleepAnalysisAsleepDeep",
          "HKCategoryValueSleepAnalysisAsleepREM"}
DISTANCE_TYPES = ("HKQuantityTypeIdentifierDistanceWalkingRunning", "HKQuantityTypeIdentifierDistanceCycling",
                  "HKQuantityTypeIdentifierDistanceDownhillSnowSports", "HKQuantityTypeIdentifierDistanceCrossCountrySkiing")
TO_M = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "ft": 0.3048, "yd": 0.9144, "cm": 0.01}
SPORTS = {"Running": "running", "Hiking": "hiking", "Walking": "walking", "Cycling": "cycling",
          "Climbing": "climbing", "CrossCountrySkiing": "cross_country_skiing",
          "DownhillSkiing": "downhill_skiing", "SnowSports": "backcountry_skiing", "Snowboarding": "snowboarding",
          "StairClimbing": "stair_climbing", "Stairs": "stair_climbing", "Elliptical": "elliptical",
          "Rowing": "rowing", "Swimming": "swimming"}
# A heart rate sample counts for route points up to this long after it.
HR_HOLD_S = 60


@dataclass
class Workout:
    sport: str
    start: datetime  # aware
    end: datetime
    distance_m: float | None = None
    gain_m: float | None = None
    route: str | None = None  # path of the route GPX inside the zip
    device: str | None = None
    hr: list[tuple[float, float]] = field(default_factory=list)  # (unix seconds, bpm)

    @property
    def source_id(self) -> str:
        return hashlib.sha256(f"{self.start.isoformat()}|{self.sport}".encode()).hexdigest()[:16]


@dataclass
class Export:
    activities: list[tuple[RemoteActivity, RawFile]]
    wellness: list[WellnessDay]
    skipped: int  # workouts with neither a route nor heart rate


def is_export(zf: zipfile.ZipFile) -> bool:
    return _xml_name(zf) is not None


def _xml_name(zf: zipfile.ZipFile) -> str | None:
    for n in zf.namelist():
        if n.endswith("export.xml") and "export_cda" not in n:
            return n
    return None


def _when(text: str) -> datetime:
    """'2026-10-09 07:12:33 -0700': wall-clock time plus the phone's zone at the time."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")


def _meters(value: str | None, unit: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value) * TO_M.get((unit or "m").strip(), 1.0)
    except ValueError:
        return None


def _quantity(text: str | None) -> float | None:
    """Metadata quantities read like '45210 cm'."""
    if not text:
        return None
    num, _, unit = text.partition(" ")
    return _meters(num, unit or "m")


def _items(zf: zipfile.ZipFile, name: str):
    """Stream the export's top-level elements, freeing each once read."""
    with zf.open(name) as f:
        events = iterparse(f, events=("start", "end"))
        _, top = next(events)
        for event, el in events:
            if event == "end" and el.tag in ("Record", "Workout"):
                yield el
                top.clear()


def read_export(path: str | Path, since: date | None = None) -> Export:
    with zipfile.ZipFile(path) as zf:
        xml = _xml_name(zf)
        if xml is None:
            raise ValueError("No export.xml in this zip: is it the Health app's export?")
        root = xml.rsplit("export.xml", 1)[0]
        workouts: list[Workout] = []
        resting: dict[date, float] = {}
        sleep: dict[tuple[date, str], float] = defaultdict(float)
        for el in _items(zf, xml):
            if el.tag == "Workout":
                w = _workout(el, root)
                if w and (since is None or w.start.date() >= since):
                    workouts.append(w)
            elif el.get("type") == RESTING_HR:
                try:
                    resting[_when(el.get("startDate")).date()] = float(el.get("value"))
                except (TypeError, ValueError):
                    pass
            elif el.get("type") == SLEEP and el.get("value") in ASLEEP:
                start, end = _when(el.get("startDate")), _when(el.get("endDate"))
                # Filed under the morning you woke, per source: a phone and a watch both log the
                # same night, and adding them would double it.
                sleep[(end.date(), el.get("sourceName") or "")] += (end - start).total_seconds()
        _heart_rate(zf, xml, workouts)
        activities, skipped = [], 0
        for w in workouts:
            raw = _route_gpx(zf, w) if w.route else None
            if raw is None:
                raw = _hr_tcx(w)
            if raw is None:
                skipped += 1
                continue
            activities.append((RemoteActivity(
                source=SOURCE, source_id=w.source_id, start_time=w.start.replace(tzinfo=None),
                name=f"{w.sport.replace('_', ' ').capitalize()} (Apple Health)", sport=w.sport,
                distance_m=w.distance_m, gain_m=w.gain_m, device_name=w.device), raw))
    nights: dict[date, float] = {}
    for (day, _), secs in sleep.items():
        nights[day] = max(nights.get(day, 0.0), secs)
    days = sorted(set(resting) | set(nights))
    wellness = [WellnessDay(source=SOURCE, day=d, resting_hr=resting.get(d), sleep_s=nights.get(d))
                for d in days if since is None or d >= since]
    return Export(activities, wellness, skipped)


def _workout(el, root: str) -> Workout | None:
    kind = (el.get("workoutActivityType") or "").removeprefix("HKWorkoutActivityType")
    try:
        start, end = _when(el.get("startDate")), _when(el.get("endDate"))
    except (TypeError, ValueError):
        return None
    w = Workout(sport=SPORTS.get(kind, kind.lower() or "other"), start=start, end=end, device=el.get("sourceName"))
    # Older exports put totals on the Workout; newer ones in WorkoutStatistics children.
    w.distance_m = _meters(el.get("totalDistance"), el.get("totalDistanceUnit"))
    for child in el:
        if child.tag == "WorkoutStatistics" and child.get("type") in DISTANCE_TYPES and w.distance_m is None:
            w.distance_m = _meters(child.get("sum"), child.get("unit"))
        elif child.tag == "MetadataEntry" and child.get("key") == "HKElevationAscended":
            w.gain_m = _quantity(child.get("value"))
        elif child.tag == "WorkoutRoute":
            ref = next((c for c in child if c.tag == "FileReference"), None)
            if ref is not None and ref.get("path"):
                w.route = root + ref.get("path").lstrip("/")
    return w


def _heart_rate(zf: zipfile.ZipFile, xml: str, workouts: list[Workout]) -> None:
    if not workouts:
        return
    spans = sorted(workouts, key=lambda w: w.start)
    starts = [w.start.timestamp() for w in spans]
    for el in _items(zf, xml):
        if el.tag != "Record" or el.get("type") != HR:
            continue
        try:
            t = _when(el.get("startDate")).timestamp()
            bpm = float(el.get("value"))
        except (TypeError, ValueError):
            continue
        i = bisect.bisect_right(starts, t) - 1
        # Workouts rarely overlap; check the latest one or two that started before this sample.
        for w in spans[max(0, i - 1): i + 1]:
            if w.start.timestamp() <= t <= w.end.timestamp():
                w.hr.append((t, bpm))
    for w in workouts:
        w.hr.sort()


def _hr_at(w: Workout, t: float) -> float | None:
    i = bisect.bisect_right(w.hr, (t, float("inf"))) - 1
    if i >= 0 and t - w.hr[i][0] <= HR_HOLD_S:
        return w.hr[i][1]
    return None


def _route_gpx(zf: zipfile.ZipFile, w: Workout) -> RawFile | None:
    try:
        root = ET.fromstring(zf.read(w.route))
    except (KeyError, ET.ParseError):
        return None
    pts = []
    for p in root.iter():
        if _local(p.tag) != "trkpt":
            continue
        t, ele = _find(p, "time"), _find(p, "ele")
        if t is None or not t.text:
            continue
        pts.append((p.get("lat"), p.get("lon"), ele.text if ele is not None else None, _time(t.text)))
    if not pts:
        return None
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="Talus from Apple Health" xmlns="http://www.topografix.com/GPX/1/1" '
           'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>']
    for lat, lon, ele, t in pts:
        bpm = _hr_at(w, t.timestamp())
        out.append(f'<trkpt lat="{escape(lat)}" lon="{escape(lon)}">'
                   + (f"<ele>{escape(ele)}</ele>" if ele else "")
                   + f"<time>{t.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</time>"
                   + (f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{bpm:.0f}</gpxtpx:hr>"
                      "</gpxtpx:TrackPointExtension></extensions>" if bpm else "")
                   + "</trkpt>")
    out.append("</trkseg></trk></gpx>")
    return RawFile("".join(out).encode(), "gpx")


def _hr_tcx(w: Workout) -> RawFile | None:
    """No route: a track of heart rate samples, with the workout's distance spread evenly over its
    time (an assumption, exact for a steady treadmill run) so pace-based loads still work."""
    if len(w.hr) < 2:
        return None
    t0, total = w.start.timestamp(), max((w.end - w.start).total_seconds(), 1.0)
    rows = []
    for t, bpm in w.hr:
        dist = (w.distance_m or 0.0) * min(max((t - t0) / total, 0.0), 1.0)
        when = datetime.fromtimestamp(t, timezone.utc)
        rows.append(f"<Trackpoint><Time>{when:%Y-%m-%dT%H:%M:%SZ}</Time><DistanceMeters>{dist:.1f}</DistanceMeters>"
                    f"<HeartRateBpm><Value>{bpm:.0f}</Value></HeartRateBpm></Trackpoint>")
    sport = {"running": "Running", "cycling": "Biking"}.get(w.sport, "Other")
    body = ('<?xml version="1.0" encoding="UTF-8"?><TrainingCenterDatabase '
            'xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"><Activities>'
            f'<Activity Sport="{sport}"><Lap><Track>{"".join(rows)}</Track></Lap></Activity></Activities>'
            "</TrainingCenterDatabase>")
    return RawFile(body.encode(), "tcx")
