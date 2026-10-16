"""Finish-time estimate for an objective: the route priced grade by grade at the athlete's own pace.

Each run's terrain analysis (terrain.py) says how many minutes a mile the athlete took on each
band of grade, climbing, flat and descending, as they naturally ran and hiked it. Pooled over
the last 90 days, those are the paces here. The route is cut into 100 m steps (route.profile),
each priced at the pace for its grade, and slowed on long days the same way the plan's time
estimate is: 2% for every hour past three, at most 20%. The athlete's slower and faster runs
on each grade (the middle half of them) give the range.

Without a GPX profile, the totals stand in: the gain and loss are spread over climbs and
descents of one steady grade, the rest flat. That gives a total, but no splits.

The estimate is moving time. Aid-station stops, weather, snow, route-finding and technical
ground are outside it, like everything else on the Plan page.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import quantiles

import numpy as np

from . import elevation, heatmap, terrain
from .plan import FADE_FROM_H, FADE_MAX, FADE_PER_H
from .units import M_PER_MI, ft, mi

HISTORY_DAYS = terrain.HISTORY_DAYS
MIN_RUNS = 3  # runs with a band before its pace is the athlete's own
MIN_ANALYSES = 3  # runs with elevation before there is any estimate
MIN_MI = 0.05  # a band share shorter than this says nothing about pace
DEFAULT_SPREAD = 0.08  # a pace from fewer runs, or estimated: 8% either way
MIN_SPREAD = 0.04
SPLIT_EVERY_MI = (1, 2, 5, 10, 20)
MAX_SPLITS = 20
TOTALS_GRADE = 0.10  # without a profile: climbs and descents at 10%...
TOTALS_UP_DOWN_SHARE = 0.8  # ...steeper when that would put more than 80% of the route on them
GRADE_WINDOW_M = 200.0  # a profile's grade over 200 m: a step is 100 m
LONGER_THAN_PROVEN = 1.5  # an estimate past 1.5 times the longest recent day is a long way out
RACE_KINDS = ("trail", "run")
TRIP_KINDS = ("trail", "run", "hike")

FLAT = ("flat", "Flat, under 3%", -terrain.FLAT_PCT, terrain.FLAT_PCT)
BANDS = [FLAT] + [(k, f"{'Up' if k.startswith('c') else 'Down'} {label.lower()}", lo, hi)
                  for k, label, lo, hi in terrain.BANDS]
# The grade each band is priced at when the athlete has no pace of their own on it.
MID_GRADE = {"flat": 0.0, "c3": 5.5, "c8": 11.5, "c15": 20.0, "c25": 30.0,
             "d3": -5.5, "d8": -11.5, "d15": -20.0, "d25": -30.0}


def counts(sport: str | None, goal: str) -> bool:
    """Whether an activity's paces count: a race reads runs, a mountain objective hikes too. A file
    that names no sport (most GPX) is taken for a run, as the plan takes it for a day on foot."""
    return not sport or heatmap.kind(sport) in (TRIP_KINDS if goal == "trip" else RACE_KINDS)


def band_of(grade_pct: float) -> str:
    for key, _, lo, hi in BANDS:
        if lo <= grade_pct < hi:
            return key
    return "c25" if grade_pct > 0 else "d25"


def _shares(analysis: dict) -> dict[str, dict]:
    """One run's time and distance on each band, flat included."""
    out = {b["key"]: b for b in analysis.get("bands") or []}
    if analysis.get("flat"):
        out["flat"] = analysis["flat"]
    return out


def paces(analyses: list[dict]) -> dict | None:
    """Minutes a mile on each band, from the runs' terrain analyses. A band with MIN_RUNS or more
    runs on it is the athlete's own: their total time over total distance there, with the middle
    half of the runs' paces for the range. The rest are worked out from the athlete's own (see
    _estimate). None until there are MIN_ANALYSES runs with elevation."""
    analyses = [a for a in analyses if a]
    if len(analyses) < MIN_ANALYSES:
        return None
    rows: dict[str, dict] = {}
    for key, label, _, _ in BANDS:
        parts = [s for a in analyses if (s := _shares(a).get(key)) and s["distance_mi"] >= MIN_MI]
        if len(parts) < MIN_RUNS:
            continue
        t = sum(p["time_s"] for p in parts) / 60
        d = sum(p["distance_mi"] for p in parts)
        each = sorted(p["time_s"] / 60 / p["distance_mi"] for p in parts)
        q = quantiles(each, n=4, method="inclusive") if len(each) >= 4 else [each[0], 0, each[-1]]
        mid = t / d
        vert = sum(p.get("vert_ft") or 0 for p in parts)
        rows[key] = {"key": key, "label": label, "source": "yours", "runs": len(parts), "pace_min_mi": mid,
                     "vert_ft_h": vert / t * 60 if key != "flat" and vert > 0 else None,
                     "fast_min_mi": min(q[0], mid * (1 - MIN_SPREAD)), "slow_min_mi": max(q[2], mid * (1 + MIN_SPREAD)),
                     "hike_share": round(sum(p["time_s"] * (p.get("hike_share") or 0) for p in parts)
                                         / sum(p["time_s"] for p in parts), 2)}
    if not rows:
        return None
    for key, label, _, _ in BANDS:
        if key not in rows:
            rows[key] = {"key": key, "label": label, "runs": 0, **_estimate(key, rows)}
    return {"runs": len(analyses), "bands": [rows[k] for k, *_ in BANDS]}


def _flat_pace(rows: dict[str, dict]) -> float:
    """The flat pace, or what the athlete's own bands say it would be (a band's pace over its grade cost)."""
    if "flat" in rows:
        return rows["flat"]["pace_min_mi"]
    own = [r for r in rows.values() if r.get("source") == "yours"]
    best = max(own, key=lambda r: r["runs"])
    return best["pace_min_mi"] / float(terrain.grade_cost(np.array(MID_GRADE[best["key"]] / 100)))


def _estimate(key: str, rows: dict[str, dict]) -> dict:
    """A band the athlete hasn't run enough of. Gentle grades and descents: the flat pace times the
    grade's cost (terrain.grade_cost). Steeper climbs are hiked at a steady vertical rate whatever
    the grade, so they take the athlete's climbing rate on the nearest climb band they have."""
    grade = MID_GRADE[key]
    flat = _flat_pace(rows)
    pace = flat * float(terrain.grade_cost(np.array(grade / 100)))
    how = "flat_cost"
    climbs = [r for k, r in rows.items() if k.startswith("c") and r.get("source") == "yours" and r["vert_ft_h"]]
    if key.startswith("c") and key != "c3" and climbs:
        near = min(climbs, key=lambda r: abs(MID_GRADE[r["key"]] - grade))
        by_rate = grade / 100 * 5280 / (near["vert_ft_h"] / 60)  # min/mi at that many feet a minute
        if by_rate > pace:
            pace, how = by_rate, "climb_rate"
    return {"source": how, "pace_min_mi": pace, "vert_ft_h": None, "fast_min_mi": pace * (1 - DEFAULT_SPREAD),
            "slow_min_mi": pace * (1 + DEFAULT_SPREAD), "hike_share": None}


def _fade(clock_h: float) -> float:
    return 1 + min(FADE_MAX, FADE_PER_H * max(0.0, clock_h - FADE_FROM_H))


def _run(steps: list[tuple[float, str]], pace_of: dict[str, float]) -> np.ndarray:
    """Minutes at the end of each step: each priced at its band's pace, slowed by the hours already out."""
    clock = np.zeros(len(steps))
    t = 0.0
    for i, (miles, band) in enumerate(steps):
        t += miles * pace_of[band] * _fade(t / 60)
        clock[i] = t
    return clock


def _profile_steps(p: dict) -> tuple[list[tuple[float, str]], np.ndarray, np.ndarray, np.ndarray]:
    dist = np.asarray(p["dist_m"], float)
    alt = np.asarray(p["alt_m"], float)
    grade = elevation.smoothed_grade(dist, alt, GRADE_WINDOW_M)
    mids = (grade[1:] + grade[:-1]) / 2
    steps = [(mi(float(d)), band_of(float(g))) for d, g in zip(np.diff(dist), mids)]
    return steps, dist[1:] / M_PER_MI, np.asarray(p["up_m"], float)[1:], np.asarray(p["down_m"], float)[1:]


def _totals_steps(distance_mi: float, gain_ft: float, loss_ft: float) -> list[tuple[float, str]]:
    """The route as climbs and descents at one steady grade, the rest flat, mixed evenly through
    it so the long-day slowing falls on each alike."""
    feet = distance_mi * 5280
    grade = max(TOTALS_GRADE, (gain_ft + loss_ft) / (TOTALS_UP_DOWN_SHARE * feet)) if feet else TOTALS_GRADE
    up, down = gain_ft / grade / 5280, loss_ft / grade / 5280
    flat = max(0.0, distance_mi - up - down)
    up_band, down_band = band_of(grade * 100), band_of(-grade * 100)
    n = max(1, int(np.ceil(distance_mi / 0.25)))
    steps = []
    for _ in range(n):
        steps += [(up / n, up_band), (flat / n, "flat"), (down / n, down_band)]
    return [s for s in steps if s[0] > 0]


def _legs(stations: list[dict], total_mi: float) -> list[tuple[float, str | None]]:
    """Where each split ends: the route's aid stations, or every 1, 2, 5, 10 or 20 mi, whichever
    gives at most MAX_SPLITS."""
    if stations:
        return [(s["mi"], s["name"]) for s in stations] + [(total_mi, "Finish")]
    every = next((e for e in SPLIT_EVERY_MI if total_mi / e <= MAX_SPLITS), SPLIT_EVERY_MI[-1])
    marks = [float(m) for m in np.arange(every, total_mi - every * 0.25, every)]
    return [(m, None) for m in marks] + [(total_mi, "Finish")]


def predict(obj, p: dict | None, profile: dict | None, longest_h: float | None = None) -> dict | None:
    """The finish-time estimate for an objective (plan.Objective), from paces() and the route's
    profile when there is one. Times are hours of moving time; splits carry minutes."""
    if p is None:
        return None
    use_profile = profile is not None and matches(profile, obj.distance_mi)
    if use_profile:
        steps, at_mi, up_m, down_m = _profile_steps(profile)
    else:
        steps = _totals_steps(obj.distance_mi, obj.gain_ft, obj.loss_ft)
    if not steps:
        return None
    rows = {b["key"]: b for b in p["bands"]}
    clocks = {k: _run(steps, {b: rows[b][f] for b in rows})
              for k, f in (("mid", "pace_min_mi"), ("low", "fast_min_mi"), ("high", "slow_min_mi"))}
    mid = clocks["mid"]

    # What the route asks of each band, and what it takes there.
    step_min = np.diff(mid, prepend=0.0)
    by_band = {}
    for (miles, band), m in zip(steps, step_min):
        b = by_band.setdefault(band, {"distance_mi": 0.0, "minutes": 0.0})
        b["distance_mi"] += miles
        b["minutes"] += float(m)
    bands = [{"key": key, "label": rows[key]["label"], "source": rows[key]["source"], "runs": rows[key]["runs"],
              "hike_share": rows[key]["hike_share"], "pace_min_mi": round(rows[key]["pace_min_mi"], 1),
              "vert_ft_h": None if rows[key]["vert_ft_h"] is None else round(rows[key]["vert_ft_h"]),
              "distance_mi": round(by_band[key]["distance_mi"], 2), "minutes": round(by_band[key]["minutes"]),
              "share": round(by_band[key]["minutes"] / float(mid[-1]), 3)}
             for key, *_ in BANDS if key in by_band and by_band[key]["distance_mi"] >= 0.01]

    splits = []
    if use_profile:
        prev_i, prev_mi = -1, 0.0
        for end_mi, name in _legs(profile.get("stations") or [], float(at_mi[-1])):
            i = min(int(np.searchsorted(at_mi, end_mi - 1e-6)), len(at_mi) - 1)
            if i > 0 and end_mi - at_mi[i - 1] < at_mi[i] - end_mi:
                i -= 1  # the step end nearest the station
            if i <= prev_i:
                continue
            up0 = 0.0 if prev_i < 0 else up_m[prev_i]
            down0 = 0.0 if prev_i < 0 else down_m[prev_i]
            t0 = 0.0 if prev_i < 0 else mid[prev_i]
            splits.append({"name": name, "from_mi": round(prev_mi, 2), "to_mi": round(float(at_mi[i]), 2),
                           "distance_mi": round(float(at_mi[i]) - prev_mi, 2),
                           "gain_ft": round(ft(up_m[i] - up0)), "loss_ft": round(ft(down_m[i] - down0)),
                           "minutes": round(float(mid[i] - t0), 1), "clock_min": round(float(mid[i]), 1),
                           "low_clock_min": round(float(clocks["low"][i]), 1),
                           "high_clock_min": round(float(clocks["high"][i]), 1)})
            prev_i, prev_mi = i, float(at_mi[i])

    mid_h = float(mid[-1]) / 60
    own_share = sum(b["share"] for b in bands if b["source"] == "yours")
    notes = []
    if not use_profile:
        notes.append("From the route's totals: upload its GPX for splits and a grade-by-grade estimate."
                     if profile is None else "The route's GPX doesn't match the objective's distance any more, so this "
                     "is from the totals. Upload the GPX again for splits.")
    if own_share < 0.8:
        notes.append(f"{round((1 - own_share) * 100)}% of the time is on grades you haven't run 3 times in the last "
                     f"{HISTORY_DAYS} days; those paces are worked out from the ones you have.")
    far = longest_h is not None and longest_h > 0 and mid_h > LONGER_THAN_PROVEN * longest_h
    if far:
        notes.append(f"It's more than {LONGER_THAN_PROVEN:g} times your longest day of the last 8 weeks, so the "
                     "slowing on a long day is a guess more than a measurement.")
    confidence = ("good" if use_profile and own_share >= 0.8 and not far and p["runs"] >= 8
                  else "rough" if own_share < 0.5 or (far and not use_profile) else "fair")
    return {"low_h": round(float(clocks["low"][-1]) / 60, 2), "mid_h": round(mid_h, 2),
            "high_h": round(float(clocks["high"][-1]) / 60, 2), "moving": True,
            "from": "profile" if use_profile else "totals", "runs": p["runs"], "days": HISTORY_DAYS,
            "confidence": confidence, "notes": notes, "bands": bands, "splits": splits,
            "fade": {"from_h": FADE_FROM_H, "per_h": FADE_PER_H, "max": FADE_MAX}}


def matches(profile: dict, distance_mi: float) -> bool:
    d = profile.get("dist_m") or []
    return len(d) >= 2 and distance_mi > 0 and abs(mi(float(d[-1])) / distance_mi - 1) <= 0.05


def history_since(today: date) -> str:
    return (today - timedelta(days=HISTORY_DAYS)).isoformat()


def valid_profile(p: dict | None) -> dict | None:
    """A profile as route.profile makes it, or None: the same length lists, distance rising."""
    if not isinstance(p, dict):
        return None
    try:
        cols = [np.asarray(p[k], float) for k in ("dist_m", "alt_m", "up_m", "down_m")]
    except (KeyError, TypeError, ValueError):
        return None
    n = len(cols[0])
    if n < 2 or n > 6000 or any(len(c) != n or not np.isfinite(c).all() for c in cols) or np.any(np.diff(cols[0]) <= 0):
        return None
    stations = [{"name": str(s.get("name"))[:60], "mi": float(s.get("mi"))} for s in (p.get("stations") or [])[:60]
                if isinstance(s, dict) and s.get("name") and isinstance(s.get("mi"), (int, float))]
    return {"dist_m": cols[0].round(1).tolist(), "alt_m": cols[1].round(1).tolist(), "up_m": cols[2].round(1).tolist(),
            "down_m": cols[3].round(1).tolist(), "stations": sorted(stations, key=lambda s: s["mi"])}
