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
    """Each system gets a state and a `reason`: a sentence fragment naming what set that state,
    load or check-in, so the call for the day never blames load for what the check-in raised."""
    engine = ledger_state(today.get("cardio"))
    climb, descent = ledger_state(today.get("climb")), ledger_state(today.get("descent"))
    legs = _worse(climb, descent)
    limiter = "descent" if STATES.index(descent) >= STATES.index(climb) else "climb"

    def vs(name: str) -> str:
        d = today.get(name)
        return describe_vs_normal(d.vs_normal if d else None)

    engine_reason = f"cardio load is {vs('cardio')}"
    legs_reason = f"{limiter} load is {vs(limiter)}"
    checkin = checkin or {}
    soreness, energy, pain = checkin.get("soreness"), checkin.get("energy"), checkin.get("pain")
    if soreness and soreness >= 4:
        raised = _worse(legs, "overreached" if soreness == 5 else "loaded")
        if raised != legs:
            legs, legs_reason = raised, f"you rated leg soreness {soreness} of 5"
    if pain and pain > 3 and legs != "overreached":
        legs, legs_reason = "overreached", f"you reported pain {pain} of 5"
    if energy and energy <= 2:
        raised = _worse(engine, "loaded")
        if raised != engine:
            engine, engine_reason = raised, f"you rated energy {energy} of 5"

    engine_detail = f"Cardio load {vs('cardio')}." + (f" Energy {energy} of 5." if energy else "")
    legs_detail = f"{limiter.capitalize()} load {vs(limiter)}." + (f" Soreness {soreness} of 5." if soreness else "")
    return {"engine": {"state": engine, "detail": engine_detail, "reason": engine_reason},
            "legs": {"state": legs, "detail": legs_detail, "limiter": limiter, "reason": legs_reason}}


def _sentence(fragment: str) -> str:
    return fragment[0].upper() + fragment[1:] + "."


def cautious_session(call: str, aet: float) -> dict:
    """The session for a call the coach made more cautious than the rules did. Sessions are
    always the engine's, so the coach can never prescribe more than the call allows."""
    if call == "rest":
        return {"session": "Rest or 30 min very easy",
                "detail": f"Walk or jog flat ground under {aet:.0f} bpm, or take the day off."}
    return {"session": "Short and easy",
            "detail": f"30 to 45 min in Zone 1, under {aet:.0f} bpm, on flat ground or hiked hills."}


def daily_call(ready: dict, checkin: dict | None, aet: float) -> dict:
    """go, modify or rest, with one session and a reason. Rule-based, and labeled so."""
    engine, legs = ready["engine"]["state"], ready["legs"]["state"]
    engine_why, legs_why = ready["engine"]["reason"], ready["legs"]["reason"]
    if checkin and checkin.get("pain") and checkin["pain"] > 3:
        return {"call": "rest", "session": "Rest day",
                "detail": "Skip training today. If the pain is still there tomorrow, see a clinician.",
                "why": f"You reported pain {checkin['pain']} of 5."}
    if "overreached" in (engine, legs):
        why = engine_why if engine == "overreached" else legs_why
        return {"call": "rest", **cautious_session("rest", aet), "why": _sentence(why)}
    if legs == "loaded" and engine == "fresh":
        return {"call": "modify", "session": "Easy aerobic, low descent",
                "detail": f"60 to 75 min in Zone 1, under {aet:.0f} bpm. Keep descent under 500 ft: "
                          "flat trail or treadmill incline.",
                "why": f"{_sentence(legs_why)} Engine is fresh, so easy volume is fine."}
    if engine == "loaded" and legs == "fresh":
        return {"call": "modify", "session": "Short and easy",
                "detail": f"30 to 45 min in Zone 1, under {aet:.0f} bpm. Hills are fine if you hike them.",
                "why": f"{_sentence(engine_why)} Legs are fresh."}
    if engine == "loaded" and legs == "loaded":
        return {"call": "modify", "session": "Easy or rest",
                "detail": f"Up to 45 min flat and under {aet:.0f} bpm, or a rest day.",
                "why": f"{_sentence(engine_why)} {_sentence(legs_why)}"}
    return {"call": "go", "session": "Train as planned",
            "detail": f"Keep easy sessions in Zone 1, under {aet:.0f} bpm.",
            "why": "Engine and legs are both fresh."}
