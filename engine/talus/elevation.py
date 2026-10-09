"""Elevation cleanup: spike removal, hysteresis gain and loss, smoothed grade.

Bad elevation is the main threat to a vert-based model, so every gain and loss number in Talus
comes from here, never from a platform's own total.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

BARO_HYSTERESIS_M = 3.0  # ~10 ft: below this, a reversal is sensor noise
GPS_HYSTERESIS_M = 6.0
LONG_CLIMB_HYSTERESIS_M = 30.0  # ~100 ft: a dip smaller than this does not end a long climb
GRADE_WINDOW_M = 100.0


def median_filter(x: np.ndarray, window: int = 5) -> np.ndarray:
    if x.size < window:
        return x.copy()
    pad = window // 2
    padded = np.pad(x, pad, mode="edge")
    return np.median(sliding_window_view(padded, window), axis=1)


def hysteresis(alt: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray, list[float], list[float]]:
    """Per-sample gain and loss (meters), plus every continuous climb and descent.

    Once climbing, each new high counts at once. A reversal only counts after it exceeds
    `threshold`, and then its full depth counts.
    """
    gain = np.zeros_like(alt)
    loss = np.zeros_like(alt)
    climbs: list[float] = []
    descents: list[float] = []
    ref, direction, run = alt[0], 0, 0.0
    for i in range(1, alt.size):
        d = alt[i] - ref
        if direction == 1 and d > 0:
            gain[i], ref, run = d, alt[i], run + d
        elif direction == -1 and d < 0:
            loss[i], ref, run = -d, alt[i], run - d
        elif d > threshold and direction != 1:
            if direction == -1:
                descents.append(run)
            gain[i], ref, direction, run = d, alt[i], 1, d
        elif -d > threshold and direction != -1:
            if direction == 1:
                climbs.append(run)
            loss[i], ref, direction, run = -d, alt[i], -1, -d
    if direction == 1:
        climbs.append(run)
    elif direction == -1:
        descents.append(run)
    return gain, loss, climbs, descents


def smoothed_grade(distance: np.ndarray, alt: np.ndarray, window_m: float = GRADE_WINDOW_M) -> np.ndarray:
    """Grade in percent at each sample, from altitude `window_m` apart along the track."""
    d_unique, idx = np.unique(distance, return_index=True)
    a_unique = alt[idx]
    if d_unique.size < 2:
        return np.zeros_like(alt)
    lo = np.clip(distance - window_m / 2, d_unique[0], d_unique[-1])
    hi = np.clip(distance + window_m / 2, d_unique[0], d_unique[-1])
    span = hi - lo
    rise = np.interp(hi, d_unique, a_unique) - np.interp(lo, d_unique, a_unique)
    with np.errstate(divide="ignore", invalid="ignore"):
        grade = np.where(span > 1, rise / span * 100, 0.0)
    return grade


def clean(alt: np.ndarray, barometric: bool) -> tuple[np.ndarray, float]:
    """Filtered altitude and the hysteresis threshold to use with it."""
    window = 5 if barometric else 9
    return median_filter(alt, window), BARO_HYSTERESIS_M if barometric else GPS_HYSTERESIS_M
