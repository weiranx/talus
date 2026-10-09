"""Core data types shared by sources, the parser and the scorer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass
class Thresholds:
    """Heart-rate anchors in bpm. AeT and AnT come from a test, the watch, or the 88% rule."""

    resting: float
    aet: float
    ant: float
    max: float
    sex: str = "male"  # sets the Banister exponent: 1.92 male, 1.67 female
    threshold_pace_s_per_mi: float | None = None  # for scoring runs without HR
    source: str = "manual"


@dataclass
class Streams:
    """Per-sample activity data, all SI. Missing channels are None."""

    time: np.ndarray  # seconds from start
    distance: np.ndarray | None = None  # meters, cumulative
    altitude: np.ndarray | None = None  # meters
    heart_rate: np.ndarray | None = None  # bpm
    cadence: np.ndarray | None = None  # steps per minute (both feet)
    lat: np.ndarray | None = None
    lon: np.ndarray | None = None
    start_time: datetime | None = None
    # Wall-clock start where the activity happened, when the file says (FIT's local_timestamp).
    # GPX and TCX carry only UTC.
    start_local: datetime | None = None
    sport: str | None = None
    barometric: bool = False


@dataclass
class Segment:
    kind: str  # "climb", "descent" or "flat"
    start_s: float
    end_s: float
    distance_m: float
    gain_m: float
    loss_m: float
    avg_grade: float  # percent, signed
    grade_band: str  # "<8", "8-20" or ">20"
    hike_share: float  # share of moving time spent hiking
    climb_load: float = 0.0  # weighted feet, same rules as the activity's climb load
    descent_load: float = 0.0


@dataclass
class ActivityScore:
    distance_mi: float
    gain_ft: float
    loss_ft: float
    effort_mi: float
    moving_time_s: float
    elapsed_time_s: float
    cardio_load: float | None
    climb_load: float
    descent_load: float
    cardio_estimated: bool
    zone_minutes: dict[str, float]  # z1 below AeT, z2 AeT to AnT, z3 above AnT
    gain_by_band_ft: dict[str, float]
    loss_by_band_ft: dict[str, float]
    hike_share: float
    longest_climb_ft: float
    longest_descent_ft: float
    minutes_above_8000ft: float
    hike_loss_by_band_ft: dict[str, float] = field(default_factory=dict)  # the hiked part of loss_by_band_ft
    segments: list[Segment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model_version: str = ""
    hr_hist: dict[str, float] | None = None  # seconds at each whole bpm, for any display zone model
