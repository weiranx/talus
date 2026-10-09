"""Score one activity: distance and vert, terrain segments, zones and the three loads."""

from __future__ import annotations

import numpy as np

from . import MODEL_VERSION, elevation, loads
from .models import ActivityScore, Segment, Streams, Thresholds
from .units import ft, m_from_ft, mi

MAX_DT_S = 10.0  # longer gaps are pauses, not training
MOVING_MPS = 0.3
HIKE_CADENCE_SPM = 135  # below this, steps are hiking
HIKE_SPEED_MPS = 1.5  # used only when there is no cadence
CLIMB_GRADE_PCT = 3.0
MIN_SEGMENT_M = 400.0  # about a quarter mile
ALTITUDE_EXPOSURE_M = m_from_ft(8000)
HIKING_SPORTS = {"hiking", "walking", "hike", "walk"}
MAX_GAIN_FT_PER_HOUR = 5000  # beyond a vertical-kilometer race pace: the elevation data is suspect


def score(s: Streams, th: Thresholds) -> ActivityScore:
    notes: list[str] = []
    dt = np.diff(s.time, prepend=s.time[0])
    dt = np.minimum(dt, MAX_DT_S)
    dd = np.diff(s.distance, prepend=s.distance[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(dt > 0, dd / dt, 0.0)
    moving = speed > MOVING_MPS
    distance_m = float(s.distance[-1] - s.distance[0])

    hiking = _hiking(s, speed)
    hike_share = float(np.sum(dt[moving & hiking]) / max(np.sum(dt[moving]), 1.0))

    if s.altitude is not None:
        alt, threshold, alt_notes = elevation.clean(s.altitude, s.barometric, s.time)
        notes.extend(alt_notes)
        if not s.barometric:
            notes.append("GPS-only elevation: gain and loss are less reliable")
        gain, loss, _, _ = elevation.hysteresis(alt, threshold)
        _, _, climbs, descents = elevation.hysteresis(alt, elevation.LONG_CLIMB_HYSTERESIS_M)
        grade = elevation.smoothed_grade(s.distance, alt)
    else:
        notes.append("No altitude data: vert and vert loads are zero")
        alt = None
        gain = loss = grade = np.zeros_like(s.time)
        climbs = descents = []

    bands = loads.band(grade)
    gain_by_band = {b: float(gain[bands == b].sum()) for b in loads.BANDS}
    loss_by_band = {b: float(loss[bands == b].sum()) for b in loads.BANDS}
    run_loss = {b: float(loss[(bands == b) & ~hiking].sum()) for b in loads.BANDS}
    hike_loss = {b: float(loss[(bands == b) & hiking].sum()) for b in loads.BANDS}

    climb_load = ft(loads.climb_load(gain_by_band))
    descent_load = ft(loads.descent_load(run_loss) + loads.descent_load(hike_loss, hiking=True))

    cardio, estimated = None, False
    zones = {"z1": 0.0, "z2": 0.0, "z3": 0.0}
    if s.heart_rate is not None:
        cardio = loads.cardio_load(s.heart_rate, dt, th)
        zones = loads.zone_minutes(s.heart_rate, dt, th)
    elif th.threshold_pace_s_per_mi:
        cardio = loads.estimated_cardio_load(speed * moving, grade, dt, th.threshold_pace_s_per_mi)
        estimated = True
        notes.append("No heart rate: cardio load estimated from grade-adjusted pace")
    else:
        notes.append("No heart rate and no threshold pace: cardio load not scored")

    gain_ft, miles = ft(float(gain.sum())), mi(distance_m)
    moving_h = float(np.sum(dt[moving])) / 3600
    if moving_h > 0.25 and gain_ft / moving_h > MAX_GAIN_FT_PER_HOUR:
        notes.append(f"{gain_ft / moving_h:,.0f} ft of gain per moving hour is not humanly possible: "
                     "check this activity's elevation against the watch")
    return ActivityScore(
        distance_mi=miles,
        gain_ft=gain_ft,
        loss_ft=ft(float(loss.sum())),
        effort_mi=loads.effort_miles(miles, gain_ft),
        moving_time_s=float(np.sum(dt[moving])),
        elapsed_time_s=float(s.time[-1] - s.time[0]),
        cardio_load=cardio,
        climb_load=climb_load,
        descent_load=descent_load,
        cardio_estimated=estimated,
        zone_minutes=zones,
        gain_by_band_ft={b: ft(v) for b, v in gain_by_band.items()},
        loss_by_band_ft={b: ft(v) for b, v in loss_by_band.items()},
        hike_share=hike_share,
        longest_climb_ft=ft(max(climbs, default=0.0)),
        longest_descent_ft=ft(max(descents, default=0.0)),
        minutes_above_8000ft=float(np.sum(dt[alt > ALTITUDE_EXPOSURE_M]) / 60) if alt is not None else 0.0,
        hike_loss_by_band_ft={b: ft(v) for b, v in hike_loss.items()},
        segments=segments(s, grade, gain, loss, hiking & moving, dt, moving, bands, hiking),
        notes=notes,
        model_version=MODEL_VERSION,
    )


def _hiking(s: Streams, speed: np.ndarray) -> np.ndarray:
    if s.sport and s.sport.lower() in HIKING_SPORTS:
        return np.ones_like(speed, dtype=bool)
    if s.cadence is not None:
        return s.cadence < HIKE_CADENCE_SPM
    return speed < HIKE_SPEED_MPS


def segments(s: Streams, grade, gain, loss, hiking, dt, moving, bands, hiking_all) -> list[Segment]:
    """Cut the track into climbs, descents and flats, absorbing pieces shorter than MIN_SEGMENT_M."""
    kind = np.where(grade > CLIMB_GRADE_PCT, "climb", np.where(grade < -CLIMB_GRADE_PCT, "descent", "flat"))
    runs: list[list[int]] = []  # [kind index start, end) as sample ranges
    start = 0
    for i in range(1, kind.size + 1):
        if i == kind.size or kind[i] != kind[start]:
            runs.append([start, i])
            start = i
    merged: list[list] = []
    for a, b in runs:
        length = s.distance[b - 1] - s.distance[a]
        k = str(kind[a])
        if merged and (length < MIN_SEGMENT_M or merged[-1][0] == k):
            merged[-1][2] = b
        else:
            merged.append([k, a, b])
    out = []
    for k, a, b in merged:
        sl = slice(a, b)
        dist = float(s.distance[b - 1] - s.distance[a])
        moving_t = float(np.sum(dt[sl][moving[sl]]))
        net = float(gain[sl].sum() - loss[sl].sum())
        avg_grade = net / dist * 100 if dist > 0 else 0.0
        out.append(Segment(
            kind=k,
            start_s=float(s.time[a]),
            end_s=float(s.time[b - 1]),
            distance_m=dist,
            gain_m=float(gain[sl].sum()),
            loss_m=float(loss[sl].sum()),
            avg_grade=avg_grade,
            grade_band=str(loads.band(avg_grade)),
            hike_share=float(np.sum(dt[sl][hiking[sl]]) / moving_t) if moving_t else 0.0,
            climb_load=ft(loads.climb_load({b: float(gain[sl][bands[sl] == b].sum()) for b in loads.BANDS})),
            descent_load=ft(
                loads.descent_load({b: float(loss[sl][(bands[sl] == b) & ~hiking_all[sl]].sum()) for b in loads.BANDS})
                + loads.descent_load({b: float(loss[sl][(bands[sl] == b) & hiking_all[sl]].sum()) for b in loads.BANDS},
                                     hiking=True)),
        ))
    return out
