"""Ten weeks of synthetic mountain training, so the web app has something to show before a
real sync. Activities are written as GPX files and go through the normal parse and score path.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from .sources.base import WellnessDay
from .units import M_PER_MI, m_from_ft

# (name, miles, feet up and down, max grade %, easy pace min/mi on the flat)
WEEK = {
    1: ("Easy flat", 6.0, 400, 6, 9.5),
    2: ("Aerobic hills", 8.2, 1900, 14, 10.5),
    3: ("Easy trail", 6.0, 300, 5, 9.5),
    5: ("Long mountain run", 13.0, 4500, 22, 11.5),
    6: ("Steep hike", 6.0, 3000, 28, 20.0),
}
CENTER = (34.289, -117.646)  # San Gabriel Mountains


def gpx(start: datetime, miles: float, gain_ft: float, max_grade: float, flat_pace: float,
        rng: random.Random) -> bytes:
    """An out-and-back: climb to the turnaround, descend home, with rolling noise."""
    total_m = miles * M_PER_MI
    half = total_m / 2
    gain_m = m_from_ft(gain_ft)
    bearing = rng.uniform(0, 2 * math.pi)
    lat0, lon0 = CENTER[0] + rng.uniform(-0.02, 0.02), CENTER[1] + rng.uniform(-0.02, 0.02)
    t, d, pts = 0.0, 0.0, []
    while d <= total_m:
        frac = d / half if d <= half else (total_m - d) / half
        # Smoothstep keeps the profile steepest mid-climb, like most mountain trails.
        alt = 1600 + gain_m * (3 * frac**2 - 2 * frac**3) + 4 * math.sin(d / 180)
        grade = gain_m * 6 * frac * (1 - frac) / half * (1 if d <= half else -1)
        grade = max(min(grade, max_grade / 100), -max_grade / 100)
        hiking = grade > 0.18 or flat_pace >= 18
        pace = flat_pace * (1 + 9 * max(grade, 0) + 1.5 * max(-grade - 0.15, 0)) * (1.6 if hiking and flat_pace < 18 else 1)
        speed = M_PER_MI / (pace * 60)
        hr = (118 + 70 * max(grade, 0) if hiking else 130 + 170 * max(grade, 0) + (8 if flat_pace < 10 else 0)) + rng.gauss(0, 2)
        cad = rng.gauss(52, 2) if hiking else rng.gauss(85, 2)
        out = d if d <= half else total_m - d
        lat = lat0 + out / 111_320 * math.cos(bearing) + 0.0006 * math.sin(d / 400)
        lon = lon0 + out / (111_320 * math.cos(math.radians(lat0))) * math.sin(bearing) + 0.0006 * math.cos(d / 300)
        ts = start + timedelta(seconds=t)
        pts.append(f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{alt:.1f}</ele>'
                   f'<time>{ts:%Y-%m-%dT%H:%M:%SZ}</time><extensions><gpxtpx:TrackPointExtension>'
                   f'<gpxtpx:hr>{min(hr, 188):.0f}</gpxtpx:hr><gpxtpx:cad>{cad:.0f}</gpxtpx:cad>'
                   f'</gpxtpx:TrackPointExtension></extensions></trkpt>')
        t += 2
        d += speed * 2
    return ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
            + "".join(pts) + "</trkseg></trk></gpx>").encode()


def sessions(today: date, weeks: int = 10, seed: int = 7):
    """Yield (start, name, gpx bytes). Two days ago is a big descent day, so legs read loaded."""
    rng = random.Random(seed)
    first = today - timedelta(days=weeks * 7)
    day = first
    while day < today:
        plan = WEEK.get(day.weekday())
        if (today - day).days == 2:
            plan = WEEK[5]
        if plan:
            name, miles, gain, grade, pace = plan
            scale = 0.85 + 0.15 * (day - first).days / (weeks * 7)  # a gentle build
            if (today - day).days == 2:
                name, miles, gain, grade = "Big ridge day", 16.0, 6500, 26
                scale = 1.0
            start = datetime.combine(day, datetime.min.time()) + timedelta(hours=14)
            yield start, name, gpx(start, miles * scale, gain * scale, grade, pace, rng)
        day += timedelta(days=1)


def wellness(today: date, weeks: int = 10, seed: int = 7) -> list[WellnessDay]:
    """Overnight HRV, resting HR and sleep for every morning, with a dip after the big ridge day
    that the 7-day HRV average absorbs, so the engine still reads fresh. One night is missing."""
    rng = random.Random(seed)
    out = []
    for k in range(weeks * 7, -1, -1):
        day = today - timedelta(days=k)
        if k == 4:
            continue  # watch not worn
        dip = 1 if k in (0, 1) else 0
        out.append(WellnessDay(source="demo", day=day, hrv=round(rng.gauss(62, 4) * (1 - 0.12 * dip), 1),
                               resting_hr=round(rng.gauss(46, 1.2) + 2 * dip),
                               sleep_s=round(rng.gauss(7.6, 0.4) * 3600 - 1200 * dip)))
    return out
