"""Overnight signals for readiness: HRV, resting heart rate and sleep, each read against the
athlete's own baseline, as the spec's readiness table sets out:

- HRV: 7-day average against a 60-day personal baseline.
- Resting HR: last night against a 60-day baseline.
- Sleep: last night and the 7-day average against the athlete's stated need.

These are whole-body signals, so they move the engine, never the legs. Like the check-in they
can only make a state worse. A missing night is labeled, not guessed, and with no data at all
readiness is exactly what loads and the check-in say. Thresholds are starting values.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from math import fsum

BASELINE_DAYS = 60
MIN_BASELINE_NIGHTS = 14  # fewer than this and the baseline is still building
RECENT_DAYS = 7
MIN_RECENT_NIGHTS = 3
HRV_LOW = 0.08  # 7-day HRV more than 8% under baseline reads as low
RHR_HIGH_BPM = 5  # last night's resting HR 5 bpm or more over baseline reads as high
SLEEP_SHORT_NIGHT_H = 1.5  # last night this far under need reads as short
SLEEP_SHORT_WEEK_H = 1.0  # or the 7-day average this far under need
DEFAULT_SLEEP_NEED_H = 8.0


def sleep_need(saved: dict | None = None) -> tuple[float, str]:
    """The athlete's stated sleep need in hours, and where it came from: the Settings screen
    ("saved"), TALUS_SLEEP_NEED_H in .env ("env"), or the 8 h default ("default")."""
    if saved and saved.get("sleep_need_h"):
        return float(saved["sleep_need_h"]), "saved"
    try:
        if os.environ.get("TALUS_SLEEP_NEED_H"):
            return float(os.environ["TALUS_SLEEP_NEED_H"]), "env"
    except ValueError:
        pass
    return DEFAULT_SLEEP_NEED_H, "default"


def mean(vals: list[float]) -> float:
    # Not statistics.mean: its exact fractions made Today's 28-day score trend slow.
    return fsum(vals) / len(vals)


def _values(days: dict[date, dict], key: str, start: date, end: date) -> list[float]:
    out, d = [], start
    while d <= end:
        v = (days.get(d) or {}).get(key)
        if v:  # 0 means the watch had nothing, not a real reading
            out.append(float(v))
        d += timedelta(days=1)
    return out


def _last_night(days: dict[date, dict], key: str, day: date) -> float | None:
    v = (days.get(day) or {}).get(key)
    return float(v) if v else None


def _baseline(days: dict[date, dict], key: str, day: date) -> tuple[float | None, int]:
    vals = _values(days, key, day - timedelta(days=BASELINE_DAYS - 1), day)
    return (mean(vals) if len(vals) >= MIN_BASELINE_NIGHTS else None), len(vals)


def _hrv(days: dict[date, dict], day: date) -> dict:
    last = _last_night(days, "hrv", day)
    recent = _values(days, "hrv", day - timedelta(days=RECENT_DAYS - 1), day)
    avg7 = mean(recent) if len(recent) >= MIN_RECENT_NIGHTS else None
    base, nights = _baseline(days, "hrv", day)
    out = {"last_night": last, "avg_7d": avg7, "baseline": base, "baseline_nights": nights,
           "vs_baseline": None}
    if not recent:
        return out | {"status": "no_data", "text": "No HRV readings this week"}
    if avg7 is None:
        return out | {"status": "no_data", "text": f"Only {len(recent)} HRV night{'s' if len(recent) > 1 else ''} this week, "
                                                   f"{MIN_RECENT_NIGHTS} needed"}
    if base is None:
        return out | {"status": "building", "text": f"Building your baseline: {nights} of {MIN_BASELINE_NIGHTS} nights"}
    vs = avg7 / base - 1
    pct = round(abs(vs) * 100)
    text = f"7-day average {pct}% {'below' if vs < 0 else 'above'} your 60-day baseline"
    if pct == 0:
        text = "7-day average in line with your 60-day baseline"
    return out | {"vs_baseline": vs, "status": "low" if vs < -HRV_LOW else "ok", "text": text}


def _resting_hr(days: dict[date, dict], day: date) -> dict:
    last = _last_night(days, "resting_hr", day)
    base, nights = _baseline(days, "resting_hr", day)
    out = {"last_night": last, "baseline": base, "baseline_nights": nights, "vs_baseline_bpm": None}
    if last is None:
        return out | {"status": "no_data", "text": "No reading last night"}
    if base is None:
        return out | {"status": "building", "text": f"Building your baseline: {nights} of {MIN_BASELINE_NIGHTS} nights"}
    delta = last - base
    bpm = round(abs(delta))
    text = f"{bpm} bpm {'above' if delta > 0 else 'below'} your 60-day baseline"
    if bpm == 0:
        text = "In line with your 60-day baseline"
    return out | {"vs_baseline_bpm": delta, "status": "high" if delta >= RHR_HIGH_BPM else "ok", "text": text}


def _sleep(days: dict[date, dict], day: date, need_h: float) -> dict:
    last_s = _last_night(days, "sleep_s", day)
    recent = _values(days, "sleep_s", day - timedelta(days=RECENT_DAYS - 1), day)
    last = None if last_s is None else last_s / 3600
    avg7 = mean(recent) / 3600 if len(recent) >= MIN_RECENT_NIGHTS else None
    out = {"last_night_h": last, "avg_7d_h": avg7, "need_h": need_h}
    if last is None and avg7 is None:
        return out | {"status": "no_data", "text": "No sleep data last night"}
    short_night = last is not None and last < need_h - SLEEP_SHORT_NIGHT_H
    short_week = avg7 is not None and avg7 < need_h - SLEEP_SHORT_WEEK_H
    parts = ["no data last night" if last is None else f"{_h(last)} last night"]
    if avg7 is not None:
        parts.append(f"{_h(avg7)} a night this week")
    text = ", ".join(parts) + f", against your {_h(need_h)} need"
    return out | {"status": "short" if short_night or short_week else "ok",
                  "short": "night" if short_night else "week" if short_week else None,
                  "text": text[0].upper() + text[1:]}


def _h(hours: float) -> str:
    m = int(round(hours * 60))
    return f"{m // 60} h {m % 60:02d} min" if m % 60 else f"{m // 60} h"


def evaluate(days: dict[date, dict], day: date, need_h: float | None = None) -> dict:
    """Read each overnight signal for `day`. `days` maps a date to that morning's record, as
    platforms file it: last night's sleep and HRV belong to the day you woke up."""
    return {"date": day.isoformat(), "hrv": _hrv(days, day), "resting_hr": _resting_hr(days, day),
            "sleep": _sleep(days, day, need_h if need_h is not None else sleep_need()[0])}


def engine_effect(w: dict | None) -> tuple[str, str] | None:
    """The engine state the overnight signals call for, with a reason, or None if they are
    quiet. HRV down and resting HR up together is the classic sign of illness or deep fatigue,
    so that pair reads as overreached; any one signal off reads as loaded."""
    if not w:
        return None
    hrv, rhr, sleep = w["hrv"], w["resting_hr"], w["sleep"]
    hrv_low, rhr_high = hrv["status"] == "low", rhr["status"] == "high"
    if hrv_low and rhr_high:
        return "overreached", (f"overnight HRV is {round(-hrv['vs_baseline'] * 100)}% below your baseline and "
                               f"resting HR is {round(rhr['vs_baseline_bpm'])} bpm above it")
    if hrv_low:
        return "loaded", f"overnight HRV is {round(-hrv['vs_baseline'] * 100)}% below your 60-day baseline"
    if rhr_high:
        return "loaded", f"resting HR is {round(rhr['vs_baseline_bpm'])} bpm above your 60-day baseline"
    if sleep["status"] == "short":
        if sleep["short"] == "night":
            return "loaded", f"you slept {_h(sleep['last_night_h'])} last night"
        return "loaded", f"you have averaged {_h(sleep['avg_7d_h'])} of sleep this week"
    return None
