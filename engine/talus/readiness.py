"""Readiness by system, and a rule-based call for the day until the coach is built.

Engine readiness comes from the cardio ledger; legs from the worse of climb and descent.
The morning check-in can only make a state worse, never better: it is the only ground truth
for leg damage. Thresholds are starting values.
"""

from __future__ import annotations

from .ledger import LedgerDay, describe_vs_normal

STATES = ("fresh", "loaded", "overreached")
LOADED_ABOVE_NORMAL = 0.15  # last 7 days more than 15% above normal reads as loaded


def ledger_state(day: LedgerDay | None) -> str:
    if day is None:
        return "fresh"
    if "overreach" in day.flags:
        return "overreached"
    if "ramp" in day.flags or (day.vs_normal or 0) > LOADED_ABOVE_NORMAL:
        return "loaded"
    return "fresh"


def _worse(a: str, b: str) -> str:
    return max(a, b, key=STATES.index)


def readiness(today: dict[str, LedgerDay], checkin: dict | None) -> dict:
    engine = ledger_state(today.get("cardio"))
    climb, descent = ledger_state(today.get("climb")), ledger_state(today.get("descent"))
    legs = _worse(climb, descent)
    limiter = "descent" if STATES.index(descent) >= STATES.index(climb) else "climb"
    if checkin:
        if checkin.get("soreness") and checkin["soreness"] >= 4:
            legs = _worse(legs, "overreached" if checkin["soreness"] == 5 else "loaded")
        if checkin.get("pain") and checkin["pain"] > 3:
            legs = "overreached"
        if checkin.get("energy") and checkin["energy"] <= 2:
            engine = _worse(engine, "loaded")

    def detail(name: str) -> str:
        d = today.get(name)
        return f"{name.capitalize()} load {describe_vs_normal(d.vs_normal if d else None)}."

    legs_detail = detail(limiter)
    if checkin and checkin.get("soreness"):
        legs_detail += f" Soreness {checkin['soreness']} of 5."
    engine_detail = detail("cardio")
    if checkin and checkin.get("energy"):
        engine_detail += f" Energy {checkin['energy']} of 5."
    lim = today.get(limiter)
    return {"engine": {"state": engine, "detail": engine_detail},
            "legs": {"state": legs, "detail": legs_detail, "limiter": limiter,
                     "limiter_vs_normal": describe_vs_normal(lim.vs_normal if lim else None)}}


def daily_call(ready: dict, checkin: dict | None, aet: float) -> dict:
    """go, modify or rest, with one session and a reason. Rule-based, and labeled so."""
    engine, legs = ready["engine"]["state"], ready["legs"]["state"]
    limiter = ready["legs"]["limiter"]
    if checkin and checkin.get("pain") and checkin["pain"] > 3:
        return {"call": "rest", "session": "Rest day",
                "detail": "Skip training today. If the pain is still there tomorrow, see a clinician.",
                "why": f"You reported pain {checkin['pain']} of 5."}
    if "overreached" in (engine, legs):
        system = "engine" if engine == "overreached" else f"{limiter} load"
        return {"call": "rest", "session": "Rest or 30 min very easy",
                "detail": f"Walk or jog flat ground under {aet:.0f} bpm, or take the day off.",
                "why": f"Your {system} is well above your normal."}
    if legs == "loaded" and engine == "fresh":
        return {"call": "modify", "session": "Easy aerobic, low descent",
                "detail": f"60 to 75 min in Zone 1, under {aet:.0f} bpm. Keep descent under 500 ft: "
                          "flat trail or treadmill incline.",
                "why": f"Your {limiter} load is {ready['legs']['limiter_vs_normal']}. "
                       "Engine is fresh, so easy volume is fine."}
    if engine == "loaded" and legs == "fresh":
        return {"call": "modify", "session": "Short and easy",
                "detail": f"30 to 45 min in Zone 1, under {aet:.0f} bpm. Hills are fine if you hike them.",
                "why": "Your cardio load is above your normal. Legs are fresh."}
    if engine == "loaded" and legs == "loaded":
        return {"call": "modify", "session": "Easy or rest",
                "detail": f"Up to 45 min flat and under {aet:.0f} bpm, or a rest day.",
                "why": "Both cardio and leg loads are above your normal."}
    return {"call": "go", "session": "Train as planned",
            "detail": f"Keep easy sessions in Zone 1, under {aet:.0f} bpm.",
            "why": "Engine and legs are both fresh."}
