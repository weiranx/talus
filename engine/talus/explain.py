"""Tap-a-number explanations: the inputs and formula behind each engine number.

Every explanation is built from the same functions and constants that made the number, so the
arithmetic shown adds up to the number on screen (within display rounding). Miles and feet.
"""

from __future__ import annotations

import numpy as np

from . import ledger, loads
from .models import ActivityScore, Streams, Thresholds

LEDGER_UNIT = {
    "cardio": "Cardio load: 100 is one hour at your threshold heart rate.",
    "climb": "Climb load: 1 is one foot gained on a moderate grade. Steeper feet count more.",
    "descent": "Descent load: 1 is one foot lost on a moderate grade. Steeper feet count more.",
}
BAND_LABEL = {"<8": "under 8%", "8-20": "8 to 20%", ">20": "over 20%"}
LEDGER_METRICS = ("load", "fatigue", "fitness", "vs_normal")
ACTIVITY_METRICS = ("effort_mi", "cardio_load", "climb_load", "descent_load",
                    "times_usual_cardio", "times_usual_climb", "times_usual_descent")


def _n(v: float, nd: int = 0) -> str:
    return f"{v:,.{nd}f}"


def _out(title: str, value: float | None, formula: str, inputs: list[tuple[str, str]],
         steps: list[str] | None = None, note: str | None = None, unit: str | None = None) -> dict:
    return {"title": title, "value": value, "unit": unit, "formula": formula,
            "inputs": [{"label": k, "value": v} for k, v in inputs], "steps": steps or [], "note": note}


# Ledgers -----------------------------------------------------------------------------------

def ledger_metric(name: str, metric: str, today: ledger.LedgerDay, yesterday: ledger.LedgerDay | None,
                  activities: list[tuple[str, float | None]]) -> dict:
    """One ledger number on one day. `activities` are (label, load) for that day."""
    label = name.capitalize()
    unit = LEDGER_UNIT[name]
    if metric == "load":
        parts = [(a, _n(v or 0)) for a, v in activities]
        steps = [" + ".join(v for _, v in parts) + f" = {_n(today.load)}"] if len(parts) > 1 else []
        return _out(f"{label} load today", today.load, "Sum of the day's activity loads. Rest days count as 0.",
                    parts or [("No activities", "0")], steps, unit=unit)
    if metric in ("fatigue", "fitness"):
        days = ledger.FATIGUE_DAYS if metric == "fatigue" else ledger.FITNESS_DAYS
        value = today.fatigue if metric == "fatigue" else today.fitness
        title = f"{label}, last 7 days a day" if metric == "fatigue" else f"{label}, your normal"
        formula = (f"A {days}-day rolling average that weights recent days more: "
                   f"today = yesterday + (today's load − yesterday) ÷ {days}.")
        if yesterday is None:
            return _out(title, value, formula, [("Today's load", _n(today.load))],
                        note="First day of history: the average starts from the opening weeks' mean load.", unit=unit)
        prev = yesterday.fatigue if metric == "fatigue" else yesterday.fitness
        return _out(title, value, formula,
                    [("Yesterday's average", _n(prev, 1)), ("Today's load", _n(today.load, 1)), ("Days", str(days))],
                    [f"{_n(prev, 1)} + ({_n(today.load, 1)} − {_n(prev, 1)}) ÷ {days} = {_n(value, 1)}"], unit=unit)
    if metric == "vs_normal":
        v = today.vs_normal
        inputs = [("Last 7 days a day", _n(today.fatigue, 1)), ("Your normal (42 days) a day", _n(today.fitness, 1))]
        if v is None:
            return _out(f"{label} vs your normal", None, "Last 7 days ÷ normal − 1.", inputs,
                        note="No normal yet, so there is nothing to compare against.")
        ratio = today.fatigue / today.fitness
        flags = (f"Flags: over {ledger.RAMP_RATIO:g}x normal reads as ramping too fast, "
                 f"over {ledger.OVERREACH_RATIO:g}x as too much.")
        return _out(f"{label} vs your normal", v, "Last 7 days ÷ normal − 1.", inputs,
                    [f"{_n(today.fatigue, 1)} ÷ {_n(today.fitness, 1)} = {ratio:.2f}",
                     f"{ratio:.2f} − 1 = {ledger.describe_vs_normal(v)}"], flags)
    raise KeyError(metric)


# Activities --------------------------------------------------------------------------------

def effort_mi(r: ActivityScore) -> dict:
    per_mile = 528
    return _out("Effort miles", r.effort_mi, f"Miles + feet gained ÷ {per_mile}: each {per_mile} ft of climbing "
                "counts as one extra mile.",
                [("Distance", f"{_n(r.distance_mi, 2)} mi"), ("Gain", f"{_n(r.gain_ft)} ft")],
                [f"{_n(r.distance_mi, 2)} + {_n(r.gain_ft)} ÷ {per_mile} = {_n(r.effort_mi, 1)}"])


def climb_load(r: ActivityScore) -> dict:
    inputs, terms = [], []
    for b in loads.BANDS:
        gain, f = r.gain_by_band_ft.get(b, 0.0), loads.CLIMB_FACTOR[b]
        inputs.append((f"Gain {BAND_LABEL[b]}", f"{_n(gain)} ft × {f:g}"))
        terms.append(f"{_n(gain * f)}")
    return _out("Climb load", r.climb_load, "Feet gained in each grade band × that band's weight, added up.",
                inputs, [" + ".join(terms) + f" = {_n(r.climb_load)}"], unit=LEDGER_UNIT["climb"])


def descent_load(r: ActivityScore) -> dict:
    inputs, terms = [], []
    hike = r.hike_loss_by_band_ft or {}
    for b in loads.BANDS:
        f = loads.DESCENT_FACTOR[b]
        hiked = hike.get(b, 0.0)
        ran = r.loss_by_band_ft.get(b, 0.0) - hiked
        if ran > 0.5 or not hiked:
            inputs.append((f"Ran down {BAND_LABEL[b]}", f"{_n(ran)} ft × {f:g}"))
            terms.append(_n(ran * f))
        if hiked > 0.5:
            hf = loads.HIKE_DESCENT_FACTOR
            inputs.append((f"Hiked down {BAND_LABEL[b]}", f"{_n(hiked)} ft × {f:g} × {hf:g}"))
            terms.append(_n(hiked * f * hf))
    return _out("Descent load", r.descent_load,
                f"Feet lost in each grade band × that band's weight, added up. Hiked descent counts "
                f"{loads.HIKE_DESCENT_FACTOR:g}x, since walking down is gentler on the quads.",
                inputs, [" + ".join(terms) + f" = {_n(r.descent_load)}"], unit=LEDGER_UNIT["descent"])


def cardio_load(r: ActivityScore, s: Streams, dt: np.ndarray, th: Thresholds) -> dict:
    unit = LEDGER_UNIT["cardio"]
    if r.cardio_load is None:
        return _out("Cardio load", None, "Needs heart rate, or a threshold pace for runs without it.", [],
                    note="Not scored: no heart rate and no threshold pace.", unit=unit)
    if r.cardio_estimated:
        pace = th.threshold_pace_s_per_mi or 0
        return _out("Cardio load (estimated)", r.cardio_load,
                    "Without heart rate: grade-adjusted pace against threshold pace. Each second adds "
                    "(pace intensity)² ÷ 3,600 × 100, so 1 hour at threshold pace = 100.",
                    [("Threshold pace", f"{int(pace // 60)}:{int(pace % 60):02d} /mi"),
                     ("Moving time", f"{_n(r.moving_time_s / 60)} min")], unit=unit)
    k = loads.BANISTER_K.get(th.sex, loads.BANISTER_K["male"])
    reserve = th.max - th.resting
    hrr = (s.heart_rate - th.resting) / reserve
    trimp = float(np.sum(dt / 60 * loads._trimp_weight(hrr, k)))
    per_hour = 60 * float(loads._trimp_weight((th.ant - th.resting) / reserve, k))
    return _out("Cardio load", r.cardio_load,
                "Banister TRIMP: each minute adds HRr × 0.64 × e^(k × HRr), where HRr = (HR − resting) ÷ "
                "(max − resting). Then scaled so one hour at your AnT scores 100.",
                [("Resting HR", f"{_n(th.resting)} bpm"), ("Max HR", f"{_n(th.max)} bpm"),
                 ("AnT", f"{_n(th.ant)} bpm"), ("k", f"{k:g} ({th.sex})"),
                 ("Average HR", f"{_n(float(np.mean(s.heart_rate)))} bpm"),
                 ("Minutes", _n(float(np.sum(dt)) / 60))],
                [f"TRIMP {_n(trimp, 1)}, one hour at AnT = {_n(per_hour, 1)}",
                 f"{_n(trimp, 1)} ÷ {_n(per_hour, 1)} × 100 = {_n(r.cardio_load)}"], unit=unit)


def times_usual(name: str, load: float | None, normal_before: float | None) -> dict:
    x = None if load is None or not normal_before else ledger.times_usual_day(load, normal_before)
    inputs = [(f"This activity's {name} load", "–" if load is None else _n(load)),
              ("Your normal the day before (42-day average)", "–" if normal_before is None else _n(normal_before, 1))]
    if x is None:
        return _out(f"{name.capitalize()} vs a usual day", None, "Activity load ÷ your normal day.", inputs,
                    note="No normal yet, or no load, so there is no ratio.")
    return _out(f"{name.capitalize()} vs a usual day", x, "Activity load ÷ your normal day going into it.", inputs,
                [f"{_n(load)} ÷ {_n(normal_before, 1)} = {x:.1f}x"])
