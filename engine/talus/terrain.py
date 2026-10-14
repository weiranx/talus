"""Mountain analysis from an activity's streams, the part a watch app doesn't do.

- Grades: how fast the athlete climbed and descended on each band of grade, in vertical feet per
  hour and min/mi, with heart rate, split into the time they ran and the time they hiked. Set
  against their own runs on the same grades (`compare`), this says whether they are climbing or
  descending better than usual, and whether hiking or running a grade costs them less.
- Durability: the first half against the second on grade-adjusted pace (grade_cost), so a run that climbs out
  and descends home doesn't read as a negative split. Grade-adjusted pace over heart rate gives
  aerobic decoupling on mountain terrain, where pace over heart rate means nothing.

Everything here comes from the same cleaned elevation and grade as the loads (elevation.py).
"""

from __future__ import annotations

from statistics import median

import numpy as np

from . import elevation
from .models import Streams
from .units import ft, mi

VERSION = 1
MAX_DT_S = 10.0  # as score.py: a longer gap is a pause
MOVING_MPS = 0.3
MIN_BAND_S = 180.0  # under 3 minutes on a grade is too little to rate
MIN_SPLIT_S = 120.0  # a hike or run share of a band needs 2 minutes to be compared

# (key, label, low, high) in percent grade. Climbs then descents, gentle to steep.
CLIMB_BANDS = [("c3", "3 to 8%", 3, 8), ("c8", "8 to 15%", 8, 15), ("c15", "15 to 25%", 15, 25),
               ("c25", "Over 25%", 25, 1000)]
DESCENT_BANDS = [("d3", "3 to 8%", -8, -3), ("d8", "8 to 15%", -15, -8), ("d15", "15 to 25%", -25, -15),
                 ("d25", "Over 25%", -1000, -25)]
BANDS = CLIMB_BANDS + DESCENT_BANDS

DURABILITY_MIN_S = 60 * 60  # an hour of moving time
DURABILITY_MIN_M = 5000.0  # about 3 mi
WARMUP_S = 10 * 60  # heart rate is still rising: left out of the first half
STEEP_CLIMB_PCT = 8.0
MIN_CLIMB_HALF_S = 10 * 60
HELD_PCT = 5.0  # decoupling under this is a held effort (the usual aerobic-decoupling rule)
FADED_PCT = 10.0

HISTORY_DAYS = 90
MIN_HISTORY = 3  # earlier runs with the same band before there is a "usual"


def grade_cost(grade: np.ndarray) -> np.ndarray:
    """How much harder a meter at this grade (a fraction) is than a flat one, judged by heart rate
    rather than by lab energy cost: uphill costs more and more, a gentle downhill is about 10%
    easier, and from about -18% descending gets harder again. The same shape as Strava's
    heart-rate-based grade-adjusted pace; the lab curve (Minetti) says a downhill is so cheap that
    every out-and-back would read as a fade, because nobody descends as fast as it allows."""
    g = np.clip(grade, -0.45, 0.45)
    return np.where(g >= 0, 1 + 3.3 * g + 3.0 * g**2, 1 + 1.9 * g + 9.5 * g**2)


def analyze(s: Streams, hiking: np.ndarray) -> dict | None:
    """Grade bands and durability for one activity, or None without altitude. hiking is the
    scorer's per-sample hiking flag (cadence, or speed without it)."""
    if s.altitude is None or s.distance is None or s.time.size < 30:
        return None
    raw_dt = np.diff(s.time, prepend=s.time[0])
    dt = np.minimum(raw_dt, MAX_DT_S)
    dd = np.diff(s.distance, prepend=s.distance[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(raw_dt > 0, dd / raw_dt, 0.0)
    moving = speed > MOVING_MPS
    alt, threshold, _ = elevation.clean(s.altitude, s.barometric, s.time)
    gain, loss, _, _ = elevation.hysteresis(alt, threshold)
    grade = elevation.smoothed_grade(s.distance, alt)
    hr = s.heart_rate
    return {"version": VERSION,
            "bands": _bands(grade, gain, loss, dd, dt, moving, hiking, hr),
            "durability": _durability(grade, gain, dd, dt, moving, hr)}


def _rate(vert_m: float, seconds: float) -> float:
    return round(ft(vert_m) / seconds * 3600)


def _pace(seconds: float, meters: float) -> float | None:
    return round(seconds / 60 / mi(meters), 2) if meters > 50 else None


def _avg_hr(hr: np.ndarray | None, dt: np.ndarray, sel: np.ndarray) -> float | None:
    if hr is None:
        return None
    w = dt[sel]
    return round(float(np.sum(hr[sel] * w) / np.sum(w)), 1) if w.sum() > 0 else None


def _bands(grade, gain, loss, dd, dt, moving, hiking, hr) -> list[dict]:
    out = []
    for key, label, lo, hi in BANDS:
        climb = key.startswith("c")
        sel = moving & (grade >= lo) & (grade < hi)
        seconds = float(dt[sel].sum())
        if seconds < MIN_BAND_S:
            continue
        vert = gain if climb else loss
        row = {"key": key, "label": label, "kind": "climb" if climb else "descent",
               "time_s": round(seconds), "distance_mi": round(mi(float(dd[sel].sum())), 2),
               "vert_ft": round(ft(float(vert[sel].sum()))), "vert_rate_ft_h": _rate(float(vert[sel].sum()), seconds),
               "pace_min_mi": _pace(seconds, float(dd[sel].sum())),
               "hike_share": round(float(dt[sel & hiking].sum()) / seconds, 2), "avg_hr": _avg_hr(hr, dt, sel)}
        for mode, m in (("hike", hiking), ("run", ~hiking)):
            part = sel & m
            t = float(dt[part].sum())
            row[mode] = None if t < MIN_SPLIT_S else {
                "time_s": round(t), "vert_rate_ft_h": _rate(float(vert[part].sum()), t),
                "pace_min_mi": _pace(t, float(dd[part].sum())), "avg_hr": _avg_hr(hr, dt, part)}
        out.append(row)
    return out


def _durability(grade, gain, dd, dt, moving, hr) -> dict | None:
    """First half against second half of the moving time, on grade-adjusted pace."""
    total = float(dt[moving].sum())
    if total < DURABILITY_MIN_S or float(dd[moving].sum()) < DURABILITY_MIN_M:
        return None
    adjusted = dd * grade_cost(grade / 100)
    clock = np.cumsum(np.where(moving, dt, 0.0))
    first = moving & (clock <= total / 2)
    second = moving & (clock > total / 2)
    steady = first & (clock > WARMUP_S)
    steep = grade >= STEEP_CLIMB_PCT

    def half(sel: np.ndarray, hr_sel: np.ndarray) -> dict:
        t = float(dt[sel].sum())
        flat_m = float(adjusted[sel].sum())
        climb = sel & steep
        ct = float(dt[climb].sum())
        out = {"time_s": round(t), "distance_mi": round(mi(float(dd[sel].sum())), 2),
               "gain_ft": round(ft(float(gain[sel].sum()))),
               "flat_pace_min_mi": _pace(t, flat_m) if flat_m > 0 else None,
               "climb_rate_ft_h": _rate(float(gain[climb].sum()), ct) if ct >= MIN_CLIMB_HALF_S else None,
               "avg_hr": _avg_hr(hr, dt, hr_sel)}
        # Grade-adjusted meters per minute per beat: the mountain version of pace over heart rate.
        ht = float(dt[hr_sel].sum())
        out["efficiency"] = (None if hr is None or not out["avg_hr"] or ht <= 0
                             else float(adjusted[hr_sel].sum()) / ht * 60 / out["avg_hr"])
        return out

    a, b = half(first, steady), half(second, second)
    pace_change = (None if not a["flat_pace_min_mi"] or not b["flat_pace_min_mi"]
                   else round((b["flat_pace_min_mi"] / a["flat_pace_min_mi"] - 1) * 100, 1))
    climb_change = (None if not a["climb_rate_ft_h"] or not b["climb_rate_ft_h"]
                    else round((b["climb_rate_ft_h"] / a["climb_rate_ft_h"] - 1) * 100, 1))
    decoupling = (None if not a["efficiency"] or not b["efficiency"]
                  else round((1 - b["efficiency"] / a["efficiency"]) * 100, 1))
    for h in (a, b):
        h["efficiency"] = None if h["efficiency"] is None else round(h["efficiency"], 3)
    t = float(dt[moving].sum())
    flat_m = float(adjusted[moving].sum())
    if decoupling is not None:
        verdict = "held" if decoupling < HELD_PCT else "drifted" if decoupling < FADED_PCT else "faded"
    elif pace_change is not None:
        verdict = "held" if pace_change < HELD_PCT else "drifted" if pace_change < FADED_PCT else "faded"
    else:
        verdict = None
    return {"first": a, "second": b, "flat_pace_min_mi": _pace(t, flat_m),
            "pace_change_pct": pace_change, "climb_rate_change_pct": climb_change,
            "decoupling_pct": decoupling, "verdict": verdict}


def compare(this: dict | None, history: list[dict]) -> dict | None:
    """Set this activity's grades and durability against earlier ones (each an `analyze` result,
    from the HISTORY_DAYS before it, same kind of activity). Adds per band the usual climbing or
    descending rate and heart rate, the change against them, and whether it was the fastest; adds
    the usual decoupling. history may hold None for activities without altitude."""
    if this is None:
        return None
    history = [h for h in history if h]
    bands = []
    for b in this["bands"]:
        prior = [x for h in history for x in h["bands"] if x["key"] == b["key"]]
        usual = None
        if len(prior) >= MIN_HISTORY:
            rate = median(x["vert_rate_ft_h"] for x in prior)
            hrs = [x["avg_hr"] for x in prior if x["avg_hr"]]
            usual = {"runs": len(prior), "vert_rate_ft_h": round(rate),
                     "avg_hr": round(median(hrs)) if hrs and b["avg_hr"] else None,
                     "rate_change_pct": round((b["vert_rate_ft_h"] / rate - 1) * 100) if rate else None,
                     "hr_change_bpm": (round(b["avg_hr"] - median(hrs)) if hrs and b["avg_hr"] else None),
                     "fastest": b["vert_rate_ft_h"] > max(x["vert_rate_ft_h"] for x in prior)}
        bands.append(b | {"usual": usual})
    dur = this["durability"]
    if dur is not None:
        prior = [h["durability"]["decoupling_pct"] for h in history
                 if h.get("durability") and h["durability"]["decoupling_pct"] is not None]
        dur = dur | {"usual_decoupling_pct": round(median(prior), 1) if len(prior) >= MIN_HISTORY else None,
                     "usual_runs": len(prior)}
    return {"bands": bands, "durability": dur, "hike_or_run": hike_or_run(bands)}


def hike_or_run(bands: list[dict]) -> list[dict]:
    """On climbs where the athlete both hiked and ran, what running bought: the change in climbing
    rate and in heart rate. Running steep ground often climbs barely faster for many more beats."""
    out = []
    for b in bands:
        h, r = b.get("hike"), b.get("run")
        if b["kind"] != "climb" or not h or not r or not h["vert_rate_ft_h"]:
            continue
        out.append({"key": b["key"], "label": b["label"],
                    "hike_rate_ft_h": h["vert_rate_ft_h"], "run_rate_ft_h": r["vert_rate_ft_h"],
                    "run_rate_gain_pct": round((r["vert_rate_ft_h"] / h["vert_rate_ft_h"] - 1) * 100),
                    "hike_hr": h["avg_hr"], "run_hr": r["avg_hr"],
                    "run_hr_cost_bpm": (round(r["avg_hr"] - h["avg_hr"]) if h["avg_hr"] and r["avg_hr"] else None)})
    return out
