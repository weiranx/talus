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
MAX_VERT_RATE_MPS = 3.0  # faster than anyone climbs or descends on foot: a jump this fast is a glitch
NOISE_WINDOW = 15  # samples; noise is what's left after a median over this window
NOISY_SD_M = 1.5  # residual noise above this is not a working barometer
SMOOTH_S = 30.0  # seconds of moving average for noisy altitude


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


def remove_jumps(alt: np.ndarray, time: np.ndarray) -> tuple[np.ndarray, int]:
    """Drop every sample-to-sample change faster than MAX_VERT_RATE_MPS and re-integrate.

    Spikes, dropouts to zero, two altitude sources interleaved and offset jumps all show up as
    changes no runner or hiker can make. Replacing them keeps the real shape of the profile. The
    result is shifted back so its median matches the original, so absolute altitude still holds.
    Returns the cleaned altitude and how many jumps were removed.
    """
    d = np.diff(alt)
    dt = np.maximum(np.diff(time), 1.0)
    bad = np.abs(d) > MAX_VERT_RATE_MPS * dt
    if not bad.any():
        return alt, 0
    # Replace each glitch with the local trend (a 30 s moving average's change, itself capped at
    # the rate limit), so a real slope under the glitches survives, e.g. two interleaved sources.
    smooth = moving_average(median_filter(alt, 2 * NOISE_WINDOW + 1), time, SMOOTH_S)
    trend = np.clip(np.diff(smooth), -MAX_VERT_RATE_MPS * dt, MAX_VERT_RATE_MPS * dt)
    d = np.where(bad, trend, d)
    out = np.concatenate([[0.0], np.cumsum(d)])
    out += np.median(alt) - np.median(out)
    return out, int(bad.sum())


def noise_sd(alt: np.ndarray) -> float:
    """Robust standard deviation of altitude around its local median, in meters."""
    if alt.size < NOISE_WINDOW * 2:
        return 0.0
    resid = alt - median_filter(alt, NOISE_WINDOW)
    return float(1.4826 * np.median(np.abs(resid - np.median(resid))))


def moving_average(x: np.ndarray, time: np.ndarray, seconds: float) -> np.ndarray:
    step = max(float(np.median(np.diff(time))) if time.size > 1 else 1.0, 0.1)
    window = int(round(seconds / step)) | 1
    if x.size < window or window < 3:
        return x.copy()
    pad = window // 2
    padded = np.pad(x, pad, mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")


def clean(alt: np.ndarray, barometric: bool, time: np.ndarray | None = None) -> tuple[np.ndarray, float, list[str]]:
    """Filtered altitude, the hysteresis threshold to use with it, and notes for the athlete.

    A file can claim a barometer and still carry GPS-grade noise, so noise is measured, not
    assumed: noisy altitude is smoothed first and gets a wider hysteresis. Then a median filter
    takes single spikes, and physically impossible jumps are replaced by the local trend.
    """
    notes: list[str] = []
    if time is None:
        time = np.arange(alt.size, dtype=float)
    noisy = noise_sd(alt) > NOISY_SD_M
    if noisy:
        # Smooth before looking for glitches: on noisy data every other sample looks like one.
        alt = moving_average(median_filter(alt, NOISE_WINDOW), time, SMOOTH_S)
        if barometric:
            notes.append("Elevation is noisier than a barometer: smoothed, so small rollers may be missed")
    # Median first: it takes single spikes and keeps sensor noise from looking like glitches.
    # (Cutting noise out of the sample-to-sample changes would turn it into a random walk.)
    alt = median_filter(alt, 5 if barometric else 9)
    alt, jumps = remove_jumps(alt, time)
    if jumps:
        notes.append(f"Removed {jumps} elevation glitches (jumps faster than a person can climb or descend)")
    threshold = BARO_HYSTERESIS_M if barometric else GPS_HYSTERESIS_M
    if noisy:
        threshold = max(threshold, GPS_HYSTERESIS_M, 3 * noise_sd(alt))
    return alt, threshold, notes
