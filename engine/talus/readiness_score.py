"""One readiness number for the day, 0 to 100, with a plain label and what moved it.

The score starts at 100 and loses points for each input that reads worse than normal: cardio
load, leg load (the worst of distance, climb and descent, so miles and vert are weighed
together), overnight HRV, resting HR and sleep against the athlete's own baselines, and the
morning check-in. An input with no data loses nothing and is listed as not used, so a score
built from load alone says so rather than looking as sure as one with a full night of data.

The number is held inside the band of the day's call (see readiness.daily_call), so it can
never read "ready" on a rest day or "rest" on a go day: the call sets the top of the band, and
the inputs set the place within it. Points are starting values, like every other threshold here.
"""

from __future__ import annotations

from .ledger import LedgerDay, describe_vs_normal
from .readiness import daily_call

# The band each call allows. Labels split the go band so a clean day reads apart from a fine one.
BANDS = {"go": (67, 100), "modify": (34, 66), "rest": (0, 33)}
LABELS = ((85, "Fresh"), (67, "Ready"), (34, "Ease off"), (0, "Rest"))

LOAD_FREE = 0.05  # last 7 days up to 5% above normal costs nothing
LOAD_FULL = 0.50  # 50% above normal costs the full points
CARDIO_MAX = 30
LEGS_MAX = 30
HRV_FREE, HRV_FULL, HRV_MAX = 0.03, 0.20, 20  # 7-day HRV below baseline
RHR_FREE, RHR_FULL, RHR_MAX = 1.0, 8.0, 15  # bpm over baseline
SLEEP_NIGHT_FREE, SLEEP_WEEK_FREE, SLEEP_PER_H, SLEEP_MAX = 0.5, 0.25, 8, 15  # hours under need
SORENESS_PTS, ENERGY_PTS, PAIN_PTS = 7, 7, 8


def label(score: int) -> str:
    return next(name for floor, name in LABELS if score >= floor)


def _ramp(value: float, free: float, full: float, most: float) -> float:
    if value <= free:
        return 0.0
    return min(most, (value - free) / (full - free) * most)


def _load_points(d: LedgerDay | None, most: float) -> float:
    if d is None or d.vs_normal is None:
        return 0.0
    pts = _ramp(d.vs_normal, LOAD_FREE, LOAD_FULL, most)
    if "overreach" in d.flags:
        pts = most
    elif "ramp" in d.flags:
        pts = max(pts, most / 2)
    return pts


def _item(key: str, name: str, points: float, text: str, used: bool = True) -> dict:
    return {"key": key, "name": name, "points": -round(points), "used": used, "text": text}


def _load_items(now: dict[str, LedgerDay]) -> list[dict]:
    cardio = now.get("cardio")
    items = [_item("cardio", "Cardio load", _load_points(cardio, CARDIO_MAX),
                   f"Last 7 days {describe_vs_normal(cardio.vs_normal if cardio else None)}", cardio is not None)]
    legs = {n: _load_points(now.get(n), LEGS_MAX) for n in ("distance", "climb", "descent")}
    worst = max(legs, key=lambda n: (legs[n], n == "descent"))  # descent wins ties: it does the damage
    parts = [f"{n} {describe_vs_normal(now[n].vs_normal)}" for n in ("distance", "climb", "descent") if n in now]
    text = ("Last 7 days: " + ", ".join(parts)) if parts else "No load history yet"
    items.append(_item("legs", "Leg load", legs[worst], text[0].upper() + text[1:], bool(parts)) | {"limiter": worst})
    return items


def _overnight_items(w: dict | None) -> list[dict]:
    if not w:
        return [_item(k, n, 0, "No data", False) for k, n in (("hrv", "HRV"), ("resting_hr", "Resting HR"),
                                                               ("sleep", "Sleep"))]
    hrv, rhr, sleep = w["hrv"], w["resting_hr"], w["sleep"]
    out = []
    if hrv.get("vs_baseline") is not None:
        out.append(_item("hrv", "HRV", _ramp(-hrv["vs_baseline"], HRV_FREE, HRV_FULL, HRV_MAX), hrv["text"]))
    else:
        out.append(_item("hrv", "HRV", 0, hrv["text"], False))
    if rhr.get("vs_baseline_bpm") is not None:
        out.append(_item("resting_hr", "Resting HR",
                         _ramp(rhr["vs_baseline_bpm"], RHR_FREE, RHR_FULL, RHR_MAX), rhr["text"]))
    else:
        out.append(_item("resting_hr", "Resting HR", 0, rhr["text"], False))
    if sleep["status"] == "no_data":
        out.append(_item("sleep", "Sleep", 0, sleep["text"], False))
    else:
        need, night, week = sleep["need_h"], sleep.get("last_night_h"), sleep.get("avg_7d_h")
        pts = max(0.0 if night is None else (need - night - SLEEP_NIGHT_FREE) * SLEEP_PER_H,
                  0.0 if week is None else (need - week - SLEEP_WEEK_FREE) * SLEEP_PER_H, 0.0)
        out.append(_item("sleep", "Sleep", min(SLEEP_MAX, pts), sleep["text"]))
    return out


def _checkin_item(c: dict | None) -> dict:
    if not c:
        return _item("checkin", "Check-in", 0, "No check-in today", False)
    soreness, energy, pain = c.get("soreness") or 1, c.get("energy") or 5, c.get("pain") or 1
    pts = (max(0, soreness - 2) * SORENESS_PTS + max(0, 3 - energy) * ENERGY_PTS + max(0, pain - 1) * PAIN_PTS)
    return _item("checkin", "Check-in", pts, f"Soreness {soreness}, energy {energy}, pain {pain} of 5")


def score(now: dict[str, LedgerDay], ready: dict, checkin: dict | None, wellness: dict | None,
          aet: float = 0.0) -> dict:
    """The day's score. `now`, `ready`, `checkin` and `wellness` are the same inputs the daily
    call reads, so the two always agree: the call sets the top of the band (100, 66 or 33), and
    each input then costs points within it, down to the band's floor."""
    rule = daily_call(ready, checkin, aet)
    call = rule["call"]
    low, high = BANDS[call]
    items = _load_items(now) + _overnight_items(wellness) + [_checkin_item(checkin)]
    if call != "go":
        items.insert(0, _item("call", f"Today's call: {'ease off' if call == 'modify' else 'rest'}", 100 - high,
                              rule["why"].rstrip(".")))
    raw = 100 + sum(i["points"] for i in items)
    value = max(low, raw)
    used = [i["name"] for i in items if i["used"] and i["key"] != "call"]
    missing = [i["name"] for i in items if not i["used"]]
    held = f"Held at {value}, the floor for a {call} day" if value != raw else None
    return {"score": value, "label": label(value), "call": call, "raw": raw, "held": held,
            "items": items, "used": used, "missing": missing, "basis": basis(used, missing)}


LOWER = {"Cardio load": "cardio load", "Leg load": "leg load", "HRV": "HRV", "Resting HR": "resting HR",
         "Sleep": "sleep", "Check-in": "check-in"}


def basis(used: list[str], missing: list[str]) -> str:
    """One line saying what the score could see."""
    if not used:
        return "No data yet: the score fills in once activities sync"
    text = "Based on " + _join([LOWER[n] for n in used])
    if {"HRV", "Resting HR", "Sleep"} <= set(missing):
        text += ". No overnight data, so it reads load" + (" and your check-in" if "Check-in" in used else "") + " only"
    return text


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
