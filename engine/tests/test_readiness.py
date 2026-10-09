from datetime import date

from talus.ledger import LedgerDay
from talus.readiness import daily_call, ledger_state, readiness


def day(ledger, fitness, fatigue, flags=()):
    return LedgerDay(date(2026, 10, 9), ledger, 0, fitness, fatigue, list(flags))


def test_ledger_states():
    assert ledger_state(day("cardio", 100, 105)) == "fresh"
    assert ledger_state(day("cardio", 100, 120)) == "loaded"
    assert ledger_state(day("cardio", 100, 160, ["overreach"])) == "overreached"


def test_engine_fresh_legs_loaded_gives_easy_low_descent():
    now = {"cardio": day("cardio", 90, 92), "climb": day("climb", 1600, 1700),
           "descent": day("descent", 1500, 2100, ["ramp"])}
    r = readiness(now, None)
    assert r["engine"]["state"] == "fresh" and r["legs"]["state"] == "loaded"
    assert r["legs"]["limiter"] == "descent"
    call = daily_call(r, None, 150)
    assert call["call"] == "modify" and "low descent" in call["session"]
    assert call["why"].startswith("Descent load is 40% above your normal.")


def test_checkin_only_makes_things_worse():
    now = {n: day(n, 100, 100) for n in ("cardio", "climb", "descent")}
    assert readiness(now, {"soreness": 1, "energy": 5, "pain": 1})["legs"]["state"] == "fresh"
    assert readiness(now, {"soreness": 4, "energy": 3, "pain": 1})["legs"]["state"] == "loaded"
    assert readiness(now, {"soreness": 2, "energy": 2, "pain": 1})["engine"]["state"] == "loaded"


def test_pain_stops_the_day():
    now = {n: day(n, 100, 100) for n in ("cardio", "climb", "descent")}
    checkin = {"soreness": 2, "energy": 4, "pain": 4}
    call = daily_call(readiness(now, checkin), checkin, 150)
    assert call["call"] == "rest" and "clinician" in call["detail"]


def test_reason_names_the_checkin_when_it_set_the_state():
    now = {n: day(n, 100, 100) for n in ("cardio", "climb", "descent")}
    sore = {"soreness": 5, "energy": 3, "pain": 1}
    assert daily_call(readiness(now, sore), sore, 150)["why"] == "You rated leg soreness 5 of 5."
    tired = {"soreness": 2, "energy": 2, "pain": 1}
    call = daily_call(readiness(now, tired), tired, 150)
    assert call["session"] == "Short and easy" and call["why"].startswith("You rated energy 2 of 5.")
    achy = {"soreness": 4, "energy": 3, "pain": 1}
    assert daily_call(readiness(now, achy), achy, 150)["why"].startswith("You rated leg soreness 4 of 5.")


def test_reason_stays_on_load_when_checkin_changes_nothing():
    now = {"cardio": day("cardio", 100, 100), "climb": day("climb", 100, 100),
           "descent": day("descent", 100, 160, ["overreach"])}
    sore = {"soreness": 5, "energy": 3, "pain": 1}
    assert daily_call(readiness(now, sore), sore, 150)["why"].startswith("Descent load is 60% above")
