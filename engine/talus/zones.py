"""Heart-rate zone models for display: 3 zones or 5, from the thresholds or edited in Settings.

Each model is a list of zone floors in whole bpm: zone k+2 starts at floors[k], zone 1 is
everything below floors[0]. Activities store a 1-bpm histogram of heart rate (hr_hist), so any
model's time in zone is read straight from it and switching models never needs a rescore.

Display zones are separate from the engine's own intensity split (below AeT, AeT to AnT, above
AnT in ActivityScore.zone_minutes). Cardio load, readiness and the 80% easy check use that split,
so changing the zone model never changes a load.
"""

from __future__ import annotations

import numpy as np

from .models import Thresholds

NAMES = {
    3: ["Easy aerobic", "The gap", "Threshold and up"],
    5: ["Recovery", "Aerobic base", "Tempo", "Threshold", "Above threshold"],
}
RECOVERY_SHARE_OF_AET = 0.90


def derived_floors(th: Thresholds, n: int) -> list[int]:
    """3 zones: AeT and just above AnT, the engine's own split.
    5 zones: 90% of AeT splits recovery from aerobic base, AeT starts tempo, the midpoint of
    the AeT-AnT gap starts threshold, and just above AnT starts zone 5. So zones 1-2 are below
    AeT and zone 5 is above AnT in both models."""
    aet, ant = round(th.aet), round(th.ant)
    if n == 3:
        floors = [aet, ant + 1]
    elif n == 5:
        floors = [round(th.aet * RECOVERY_SHARE_OF_AET), aet, round((aet + ant) / 2), ant + 1]
    else:
        raise ValueError(f"No {n}-zone model")
    for i in range(1, len(floors)):  # a tiny aerobic gap could make two floors meet
        floors[i] = max(floors[i], floors[i - 1] + 1)
    return floors


def valid_floors(floors: list[int], n: int) -> bool:
    return len(floors) == n - 1 and all(40 <= f <= 240 for f in floors) and all(
        b > a for a, b in zip(floors, floors[1:]))


def model(th: Thresholds, saved: dict | None) -> dict:
    """The zone model in use. saved is the hr_zones setting: {"zones": 3 | 5, "custom": {"3": [...], "5": [...]}}."""
    saved = saved or {}
    n = saved.get("zones", 3)
    custom = (saved.get("custom") or {}).get(str(n))
    derived = derived_floors(th, n)
    floors = custom if custom and valid_floors(custom, n) else derived
    return {"zones": n, "floors": floors, "derived": derived, "custom": floors != derived,
            "names": NAMES[n], "list": describe(floors, n)}


def describe(floors: list[int], n: int) -> list[dict]:
    out = []
    for k in range(n):
        low = None if k == 0 else floors[k - 1]
        high = None if k == n - 1 else floors[k] - 1
        text = (f"under {floors[0]} bpm" if low is None else f"{low} bpm and up" if high is None
                else f"{low} to {high} bpm")
        out.append({"zone": k + 1, "name": NAMES[n][k], "low": low, "high": high, "text": text})
    return out


def hr_hist(hr: np.ndarray, dt_s: np.ndarray) -> dict[str, float]:
    """Seconds spent at each whole bpm."""
    bpm = np.floor(hr).astype(int)
    out: dict[str, float] = {}
    for b in np.unique(bpm):
        out[str(int(b))] = round(float(np.sum(dt_s[bpm == b])), 1)
    return out


def minutes(hist: dict[str, float] | None, floors: list[int]) -> dict[str, float] | None:
    if hist is None:
        return None
    out = {f"z{k + 1}": 0.0 for k in range(len(floors) + 1)}
    for b, s in hist.items():
        out[f"z{index(int(b), floors)}"] += s / 60
    return out


def index(bpm: float, floors: list[int]) -> int:
    return int(np.searchsorted(floors, np.floor(bpm), side="right")) + 1


def indices(hr: np.ndarray, floors: list[int]) -> np.ndarray:
    return np.searchsorted(floors, np.floor(hr), side="right") + 1
