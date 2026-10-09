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

from . import elevation
from .models import Streams
from .score import MAX_DT_S, MOVING_MPS

FOOT_SPORTS = {"running", "trail_running", "run", "hiking", "hike", "walking", "walk", "generic"}
ANT_HOLD_S = 3600
ANT_SHORT_S = 1800
ANT_SHORT_FACTOR = 0.97
DRIFT_MAX = 0.05  # pace:HR decoupling under this means the run stayed aerobic
DRIFT_MIN_S = 40 * 60  # flat moving time needed to read drift
WARMUP_S = 10 * 60
FLAT_GRADE_PCT = 3.0
STEADY_CV = 0.06  # heart rate must stay this steady (sd / mean) for drift to mean anything
PROVISIONAL_AET = 0.88
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


def best_average(hr: np.ndarray, time: np.ndarray, seconds: float) -> float | None:
    """Highest time-weighted average heart rate over any `seconds` of the activity.
    Pauses longer than MAX_DT_S count as MAX_DT_S, so standing around can't stretch a window."""
    dt = np.minimum(np.diff(time, prepend=time[0]), MAX_DT_S)
    t, w = _cum(hr, dt)
    if t[-1] < seconds:
        return None
    end = np.searchsorted(t, t + seconds)
    ok = end < t.size
    start = np.nonzero(ok)[0]
    end = end[ok]
    return float(np.max((w[end] - w[start]) / (t[end] - t[start])))


def held_max(hr: np.ndarray, time: np.ndarray) -> float | None:
    return best_average(hr, time, MAX_SPIKE_S)


def drift(s: Streams) -> tuple[float, float, float] | None:
    """Pace:HR decoupling on the flat, moving part of a run after warm-up:
    (first-half average HR, decoupling, flat minutes), or None when there isn't enough flat time or the
    heart rate wasn't steady enough to read."""
    if s.heart_rate is None or s.distance is None:
        return None
    dt = np.minimum(np.diff(s.time, prepend=s.time[0]), MAX_DT_S)
    dd = np.diff(s.distance, prepend=s.distance[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(dt > 0, dd / dt, 0.0)
    if s.altitude is not None:
        alt, _, _ = elevation.clean(s.altitude, s.barometric, s.time)
        grade = elevation.smoothed_grade(s.distance, alt)
    else:
        grade = np.zeros_like(s.time)
    use = (speed > MOVING_MPS) & (np.abs(grade) < FLAT_GRADE_PCT) & (s.time > WARMUP_S)
    if np.sum(dt[use]) < DRIFT_MIN_S:
        return None
    hr_used = s.heart_rate[use]
    if np.std(hr_used) / np.mean(hr_used) > STEADY_CV:
        return None  # intervals or a workout with a cool-down, not a steady run
    t_used = np.cumsum(np.where(use, dt, 0.0))
    first = use & (t_used <= t_used[-1] / 2)
    second = use & ~first

    def ef(m: np.ndarray) -> float:  # efficiency factor: distance per heartbeat
        return float(np.sum(dd[m]) / np.sum(s.heart_rate[m] * dt[m]))

    e1, e2 = ef(first), ef(second)
    # Read the run at its first-half heart rate: the effort before any drift set in.
    avg_hr = float(np.sum(s.heart_rate[first] * dt[first]) / np.sum(dt[first]))
    return avg_hr, (e1 - e2) / e1, float(np.sum(dt[use]) / 60)


def infer(activities: list[tuple[int, Streams]]) -> dict:
    """Estimates for max, AnT and AeT from (activity id, streams) pairs, plus the zones they
    imply and the drift reading of every run that had one."""
    foot = [(i, s) for i, s in activities
            if s.heart_rate is not None and (s.sport or "generic").lower() in FOOT_SPORTS]

    maxes = [(i, held_max(s.heart_rate, s.time)) for i, s in foot]
    maxes = [(i, v) for i, v in maxes if v is not None]
    if maxes:
        top_id, top = max(maxes, key=lambda x: x[1])
        mx = Estimate(round(top), "medium" if len(foot) >= 10 else "low",
                      f"highest heart rate held {MAX_SPIKE_S} s; a true max may be higher", [top_id])
    else:
        mx = Estimate(None, "none", "no runs with heart rate")

    efforts = []
    for i, s in foot:
        hour = best_average(s.heart_rate, s.time, ANT_HOLD_S)
        half = best_average(s.heart_rate, s.time, ANT_SHORT_S)
        cands = [v for v in (hour, None if half is None else half * ANT_SHORT_FACTOR) if v is not None]
        if cands:
            efforts.append((i, max(cands)))
    if efforts:
        efforts.sort(key=lambda x: x[1], reverse=True)
        best_id, best = efforts[0]
        backed = [i for i, v in efforts if best - v <= 3]
        hard_enough = mx.value is not None and best >= HARD_SHARE_OF_MAX * mx.value
        confidence = "medium" if len(backed) >= 2 and hard_enough else "low"
        why = "best hour of heart rate, or best 30 min less 3%"
        if not hard_enough:
            why += "; no run looks like a hard sustained effort, so this likely reads low"
        ant = Estimate(round(best), confidence, why, backed[:5] if len(backed) > 1 else [best_id])
    else:
        ant = Estimate(None, "none", "no run with 30 min of heart rate")

    drifts = []
    for i, s in foot:
        d = drift(s)
        if d is not None:
            drifts.append(Drift(i, round(d[0], 1), round(d[1], 4), round(d[2], 1)))
    steady = [d for d in drifts if d.decoupling < DRIFT_MAX and (ant.value is None or d.avg_hr < ant.value)]
    drifted = [d for d in drifts if d.decoupling >= DRIFT_MAX]
    if steady:
        floor = max(steady, key=lambda d: d.avg_hr)
        ceiling = [d for d in drifted if d.avg_hr > floor.avg_hr]
        ids = [floor.activity_id]
        if ceiling:
            c = min(ceiling, key=lambda d: d.avg_hr)
            ids.append(c.activity_id)
            confidence = "high" if c.avg_hr - floor.avg_hr <= 10 else "medium"
            method = (f"held under {DRIFT_MAX:.0%} drift at {floor.avg_hr:.0f} bpm, "
                      f"drifted at {c.avg_hr:.0f} bpm")
        else:
            confidence = "low"
            method = (f"held under {DRIFT_MAX:.0%} drift at {floor.avg_hr:.0f} bpm; no run drifted, "
                      "so AeT may be higher")
        aet = Estimate(round(floor.avg_hr), confidence, method, ids)
    elif ant.value is not None:
        aet = Estimate(round(ant.value * PROVISIONAL_AET), "provisional",
                       f"{PROVISIONAL_AET:.0%} of AnT: no steady flat hour to read drift from")
    else:
        aet = Estimate(None, "none", "no heart rate data")

    zones = None
    if aet.value and ant.value:
        zones = {"z1": f"under {aet.value} bpm", "z2": f"{aet.value} to {ant.value} bpm",
                 "z3": f"over {ant.value} bpm"}
    return {"max": mx, "ant": ant, "aet": aet, "zones": zones, "drift_runs": drifts,
            "runs_with_hr": len(foot)}
