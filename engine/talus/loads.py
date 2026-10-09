"""The three load ledgers' per-activity inputs: cardio, climb and descent.

Factors are the spec's starting values, not validated constants. Phase 2 tunes them per athlete.
"""

from __future__ import annotations

import numpy as np

from .models import Thresholds

BANDS = ("<8", "8-20", ">20")
CLIMB_FACTOR = {"<8": 0.75, "8-20": 1.0, ">20": 1.25}
DESCENT_FACTOR = {"<8": 0.5, "8-20": 1.0, ">20": 1.5}
HIKE_DESCENT_FACTOR = 0.7
BANISTER_K = {"male": 1.92, "female": 1.67}


def band(grade_pct: float | np.ndarray):
    g = np.abs(grade_pct)
    return np.where(g < 8, "<8", np.where(g <= 20, "8-20", ">20"))


def climb_load(gain_by_band: dict[str, float]) -> float:
    return sum(gain_by_band.get(b, 0.0) * CLIMB_FACTOR[b] for b in BANDS)


def descent_load(loss_by_band: dict[str, float], hiking: bool = False) -> float:
    mode = HIKE_DESCENT_FACTOR if hiking else 1.0
    return sum(loss_by_band.get(b, 0.0) * DESCENT_FACTOR[b] for b in BANDS) * mode


def _trimp_weight(hrr, k: float):
    hrr = np.clip(hrr, 0.0, 1.0)
    return hrr * 0.64 * np.exp(k * hrr)


def cardio_load(hr: np.ndarray, dt_s: np.ndarray, th: Thresholds) -> float:
    """Banister TRIMP, rescaled so one hour at AnT scores 100."""
    k = BANISTER_K.get(th.sex, BANISTER_K["male"])
    reserve = th.max - th.resting
    hrr = (hr - th.resting) / reserve
    hrr_ant = (th.ant - th.resting) / reserve
    per_hour_at_ant = 60 * float(_trimp_weight(hrr_ant, k))
    trimp = float(np.sum(dt_s / 60 * _trimp_weight(hrr, k)))
    return trimp / per_hour_at_ant * 100


def minetti_cost(grade_frac):
    """Energy cost of running (J/kg/m) at a grade, Minetti et al. 2002."""
    i = np.clip(grade_frac, -0.45, 0.45)
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def estimated_cardio_load(speed_mps: np.ndarray, grade_pct: np.ndarray, dt_s: np.ndarray,
                          threshold_pace_s_per_mi: float) -> float:
    """Fallback without heart rate: grade-adjusted pace against threshold pace, 100 = 1 h at threshold."""
    gap = speed_mps * minetti_cost(grade_pct / 100) / minetti_cost(0.0)
    threshold_speed = 1609.344 / threshold_pace_s_per_mi
    intensity = gap / threshold_speed
    return float(np.sum(dt_s / 3600 * intensity**2) * 100)


def effort_miles(miles: float, gain_ft: float) -> float:
    return miles + gain_ft / 528


def zone_minutes(hr: np.ndarray, dt_s: np.ndarray, th: Thresholds) -> dict[str, float]:
    return {
        "z1": float(np.sum(dt_s[hr < th.aet]) / 60),
        "z2": float(np.sum(dt_s[(hr >= th.aet) & (hr <= th.ant)]) / 60),
        "z3": float(np.sum(dt_s[hr > th.ant]) / 60),
    }
