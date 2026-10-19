"""Infer heart-rate thresholds from the athlete's own runs.

- AnT: the highest heart rate held for an hour. From the best 60-minute average, or the best
  30-minute average less 3%, since a half-hour effort runs a little above threshold.
- AeT: Uphill Athlete's drift test, read from ordinary runs. On a steady, flat-ish hour below
  AeT, pace-to-heart-rate drifts less than 5% from the first half to the second. The highest
  average heart rate that still holds under 5% is a floor for AeT; the lowest that drifts more
  is a ceiling. Without a qualifying run, AeT falls back to 88% of AnT, marked provisional.
- Max: the highest heart rate recorded, ignoring brief spikes. A true max may be higher.

Every estimate names the activities it came from and how far to trust it. Estimates are
starting values; a lab or field test always wins, and `talus thresholds` overrides them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import MODEL_VERSION, elevation
from .models import Streams
from .score import MAX_DT_S, MOVING_MPS

# Lower-cased: intervals.icu sends "Run", "TrailRun", "VirtualRun", "Hike", "Walk".
FOOT_SPORTS = {"running", "trail_running", "run", "trailrun", "virtualrun", "hiking", "hike", "walking", "walk",
               "generic"}
ANT_HOLD_S = 3600
ANT_SHORT_S = 1800
ANT_SHORT_FACTOR = 0.97
DRIFT_MAX = 0.05  # pace:HR decoupling under this means the run stayed aerobic
DRIFT_MIN_S = 40 * 60  # flat moving time needed to read drift
WARMUP_S = 10 * 60
FLAT_GRADE_PCT = 3.0
STEADY_CV = 0.06  # heart rate must stay this steady (sd / mean) for drift to mean anything
PROVISIONAL_AET = 0.88
# Bump when `features` changes, so stored ones are read again. Elevation cleanup feeds the drift
# reading, so a new scoring model reads them again too.
FEATURES_VERSION = f"2-{MODEL_VERSION}"
HARD_SHARE_OF_MAX = 0.93  # a best hour this close to the highest HR recorded looks like a real hard effort
MAX_SPIKE_S = 5  # a max must be held this long to count


@dataclass
class Estimate:
    value: float | None
    confidence: str  # high, medium, low, provisional, none
    method: str
    activities: list[int] = field(default_factory=list)


@dataclass
class Drift:
    activity_id: int
    avg_hr: float
    decoupling: float
    flat_minutes: float


def _cum(hr: np.ndarray, dt: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.concatenate([[0.0], np.cumsum(dt)]), np.concatenate([[0.0], np.cumsum(hr * dt)])


def _blocks(mask: np.ndarray) -> list[np.ndarray]:
    indices = np.flatnonzero(mask)
    return [b for b in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1) if b.size]


def _valid_hr(hr: np.ndarray) -> np.ndarray:
    return np.isfinite(hr) & (hr >= 30) & (hr <= 240)


def best_average(hr: np.ndarray, time: np.ndarray, seconds: float) -> float | None:
    """Exact-duration HR average on continuous data; pauses and invalid samples break efforts.

    Each sample describes the interval ending at its timestamp. Integrate fractional
    edge intervals and consider windows starting or ending at every interval boundary.
    """
    if len(time) < 2 or len(hr) != len(time) or seconds <= 0:
        return None
    dt = np.diff(time, prepend=time[0])
    valid = (_valid_hr(hr) & np.isfinite(time) & np.isfinite(dt)
             & (dt > 0) & (dt <= MAX_DT_S))
    best = None
    for block in _blocks(valid):
        t, w = _cum(hr[block], dt[block])
        if t[-1] < seconds:
            continue
        starts = np.unique(np.concatenate((t[t <= t[-1] - seconds], t[t >= seconds] - seconds)))
        averages = (np.interp(starts + seconds, t, w) - np.interp(starts, t, w)) / seconds
        candidate = float(np.max(averages))
        best = candidate if best is None else max(best, candidate)
    return best


def held_max(hr: np.ndarray, time: np.ndarray) -> float | None:
    return best_average(hr, time, MAX_SPIKE_S)


def drift(s: Streams) -> tuple[float, float, float] | None:
    """Pace:HR decoupling on the flat, moving part of a run after warm-up:
    (first-half average HR, decoupling, flat minutes), or None when there isn't enough flat time or the
    heart rate wasn't steady enough to read."""
    if s.heart_rate is None or s.distance is None or s.altitude is None or len(s.time) < 2:
        return None
    raw_dt = np.diff(s.time, prepend=s.time[0])
    dt = np.minimum(raw_dt, MAX_DT_S)
    dd = np.diff(s.distance, prepend=s.distance[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(raw_dt > 0, dd / raw_dt, 0.0)  # over the real gap, as in score
    alt, _, _ = elevation.clean(s.altitude, s.barometric, s.time)
    grade = elevation.smoothed_grade(s.distance, alt)
    use = ((speed > MOVING_MPS) & np.isfinite(speed) & (np.abs(grade) < FLAT_GRADE_PCT)
           & (s.time - s.time[0] > WARMUP_S) & (raw_dt > 0) & (raw_dt <= MAX_DT_S)
           & _valid_hr(s.heart_rate))
    candidates = []
    for block in _blocks(use):
        weights = dt[block]
        duration = float(np.sum(weights))
        if duration < DRIFT_MIN_S:
            continue
        hr, pace = s.heart_rate[block], speed[block]

        def cv(values: np.ndarray) -> float:
            mean = np.average(values, weights=weights)
            return float(np.sqrt(np.average((values - mean) ** 2, weights=weights)) / mean)

        if cv(hr) > STEADY_CV or cv(pace) > 0.10:
            continue
        end = np.cumsum(weights)
        first = np.clip(duration / 2 - (end - weights), 0, weights)
        second = weights - first
        e1 = np.sum(pace * first) / np.sum(hr * first)
        e2 = np.sum(pace * second) / np.sum(hr * second)
        decoupling = float((e1 - e2) / e1)
        if decoupling < -DRIFT_MAX:
            continue  # substantial negative drift suggests pacing/warm-up confounding
        candidates.append((float(np.average(hr, weights=first)), decoupling, duration / 60))
    return max(candidates, key=lambda d: d[2]) if candidates else None


def features(s: Streams) -> dict | None:
    """What one run contributes to the estimates, so it can be stored and never re-read: the
    highest held HR, the best hour and half hour, and the drift reading. None for anything that
    isn't a run, walk or hike with heart rate."""
    if s.heart_rate is None or (s.sport or "generic").lower() not in FOOT_SPORTS:
        return None
    d = drift(s)
    valid_hr = s.heart_rate[_valid_hr(s.heart_rate)]
    if not valid_hr.size:
        return None
    return {"low_hr": float(np.percentile(valid_hr, 20)),
            "max": held_max(s.heart_rate, s.time),
            "hour": best_average(s.heart_rate, s.time, ANT_HOLD_S),
            "half": best_average(s.heart_rate, s.time, ANT_SHORT_S),
            "drift": None if d is None else list(d)}


def infer(activities: list[tuple[int, Streams]]) -> dict:
    """Estimates for max, AnT and AeT from (activity id, streams) pairs, plus the zones they
    imply and the drift reading of every run that had one."""
    return combine([(i, features(s)) for i, s in activities])


def combine(runs: list[tuple[int, dict | None]]) -> dict:
    """The estimates from each run's stored `features`."""
    foot = [(i, f) for i, f in runs if f is not None]

    maxes = [(i, f["max"]) for i, f in foot if f["max"] is not None]
    if maxes:
        top_id, top = max(maxes, key=lambda x: x[1])
        mx = Estimate(round(top), "medium" if len(foot) >= 10 else "low",
                      f"highest heart rate held {MAX_SPIKE_S} s; a true max may be higher", [top_id])
    else:
        mx = Estimate(None, "none", "no runs with heart rate")

    efforts = []
    for i, f in foot:
        cands = [v for v in (f["hour"], None if f["half"] is None else f["half"] * ANT_SHORT_FACTOR)
                 if v is not None]
        if cands:
            efforts.append((i, max(cands)))
    if efforts:
        efforts.sort(key=lambda x: x[1], reverse=True)
        best_id, best = efforts[0]
        backed = [i for i, v in efforts if best - v <= 3]
        # An observed max from easy runs alone cannot establish a hard effort.
        lower = [f["low_hr"] for _, f in foot if f.get("low_hr") is not None]
        contrast = bool(lower) and best - min(lower) >= 10
        hard_enough = mx.value is not None and best >= HARD_SHARE_OF_MAX * mx.value and contrast
        confidence = "medium" if len(backed) >= 2 and hard_enough else "low"
        why = "best hour of heart rate, or best 30 min less 3%"
        if not hard_enough:
            why += "; no run looks like a hard sustained effort, so this likely reads low"
        ant = Estimate(round(best), confidence, why, backed[:5] if len(backed) > 1 else [best_id])
    else:
        ant = Estimate(None, "none", "no run with 30 min of heart rate")

    # Classify using full precision: rounding can flip the 5% boundary.
    drifts = [Drift(i, *f["drift"])
              for i, f in foot if f["drift"] is not None]
    steady = [d for d in drifts if -DRIFT_MAX <= d.decoupling < DRIFT_MAX and (ant.value is None or d.avg_hr < ant.value)]
    drifted = [d for d in drifts if d.decoupling >= DRIFT_MAX]
    if steady:
        floor = max(steady, key=lambda d: d.avg_hr)
        ceiling = [d for d in drifted if d.avg_hr > floor.avg_hr]
        ids = [floor.activity_id]
        if ceiling:
            c = min(ceiling, key=lambda d: d.avg_hr)
            ids.append(c.activity_id)
            confidence = "medium"  # ordinary runs are not controlled threshold tests
            method = (f"held under {DRIFT_MAX:.0%} drift at {floor.avg_hr:.0f} bpm, "
                      f"drifted at {c.avg_hr:.0f} bpm")
        else:
            confidence = "low"
            method = (f"held under {DRIFT_MAX:.0%} drift at {floor.avg_hr:.0f} bpm; no higher-HR run established a ceiling, "
                      "so AeT may be higher")
        conflicts = [d for d in drifted if d.avg_hr <= floor.avg_hr]
        if conflicts:
            confidence = "low"
            method += "; conflicting drift at lower heart rates: confirm with a steady field test"
            ids.extend(d.activity_id for d in conflicts[:3])
        value = min(round(floor.avg_hr), ant.value - 1) if ant.value else round(floor.avg_hr)
        aet = Estimate(value, confidence, method, ids)
    elif ant.value is not None:
        aet = Estimate(round(ant.value * PROVISIONAL_AET), "provisional",
                       f"{PROVISIONAL_AET:.0%} of AnT: no qualifying steady flat effort to read drift from")
    else:
        aet = Estimate(None, "none", "not enough qualifying heart-rate data to estimate AeT")

    zones = None
    if aet.value and ant.value:
        zones = {"z1": f"under {aet.value} bpm", "z2": f"{aet.value} to {ant.value} bpm",
                 "z3": f"over {ant.value} bpm"}
    return {"max": mx, "ant": ant, "aet": aet, "zones": zones, "drift_runs": drifts,
            "runs_with_hr": len(foot)}
