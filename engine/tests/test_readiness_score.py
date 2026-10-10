from datetime import date, timedelta

from talus.ledger import LedgerDay
from talus.readiness import daily_call, readiness
from talus.readiness_score import BANDS, label, score
from talus.wellness import evaluate


def day(ledger, fitness, fatigue, flags=()):
    return LedgerDay(date(2026, 10, 9), ledger, 0, fitness, fatigue, list(flags))


def even(**over):
    now = {n: day(n, 100, 100) for n in ("cardio", "distance", "climb", "descent")}
    now.update(over)
    return now


def run(now, checkin=None, w=None):
    return score(now, readiness(now, checkin, w), checkin, w, 150)


def nights(hrv=45.0, rhr=55.0, sleep_h=8.0, recent_hrv=None, recent_rhr=None, recent_sleep=None, n=60):
    end = date(2026, 10, 9)
    out = {}
    for i in range(n):
        d = end - timedelta(days=i)
        r = i < 7
        out[d] = {"hrv": recent_hrv if r and recent_hrv else hrv,
                  "resting_hr": recent_rhr if i == 0 and recent_rhr else rhr,
                  "sleep_s": (recent_sleep if r and recent_sleep else sleep_h) * 3600}
    return out


def test_normal_day_with_no_overnight_data_is_100_and_says_so():
    s = run(even())
    assert s["score"] == 100 and s["label"] == "Fresh" and s["call"] == "go"
    assert set(s["missing"]) == {"HRV", "Resting HR", "Sleep", "Check-in"}
    assert "No overnight data" in s["basis"]


def test_full_data_lists_every_input_used():
    w = evaluate(nights(), date(2026, 10, 9), 8.0)
    s = run(even(), {"soreness": 2, "energy": 4, "pain": 1}, w)
    assert s["score"] == 100 and s["missing"] == []
    assert s["basis"].startswith("Based on cardio load, leg load, HRV, resting HR, sleep and check-in")


def test_score_always_sits_in_the_band_of_the_call():
    cases = [
        even(),
        even(descent=day("descent", 100, 140, ["ramp"])),
        even(cardio=day("cardio", 100, 160, ["overreach"])),
        even(distance=day("distance", 5, 7.5)),
    ]
    for now in cases:
        for c in (None, {"soreness": 5, "energy": 1, "pain": 2}, {"soreness": 1, "energy": 5, "pain": 5}):
            s = run(now, c)
            call = daily_call(readiness(now, c), c, 150)["call"]
            low, high = BANDS[call]
            assert s["call"] == call and low <= s["score"] <= high


def test_distance_counts_with_the_vert():
    s = run(even(distance=day("distance", 5, 7.0)))  # 40% more miles, same vert
    legs = next(i for i in s["items"] if i["key"] == "legs")
    assert legs["limiter"] == "distance" and legs["points"] < 0
    assert s["call"] == "go" and s["score"] < 100


def test_low_hrv_and_short_sleep_cost_points_and_name_why():
    w = evaluate(nights(recent_hrv=36.0, recent_sleep=6.3), date(2026, 10, 9), 8.0)
    s = run(even(), None, w)
    lost = {i["key"]: i for i in s["items"] if i["points"] < 0}
    assert {"hrv", "sleep"} <= set(lost)
    assert s["call"] == "modify" and 34 <= s["score"] <= 66
    assert "below your 60-day baseline" in lost["hrv"]["text"]


def test_call_sets_the_top_of_the_band_and_inputs_place_it():
    s = run(even(descent=day("descent", 100, 140, ["ramp"])))
    assert s["call"] == "modify" and s["items"][0]["key"] == "call" and s["items"][0]["points"] == -34
    assert s["score"] == 100 + sum(i["points"] for i in s["items"]) and s["score"] < 66
    assert s["items"][0]["name"] == "Today's call: ease off"
    assert s["items"][0]["text"].startswith("Descent load is 40% above your normal")


def test_held_at_the_floor():
    c = {"soreness": 5, "energy": 1, "pain": 5}
    s = run(even(), c)
    assert s["call"] == "rest" and s["score"] == 0 and s["raw"] < 0 and s["held"]


def test_labels():
    assert [label(v) for v in (100, 85, 84, 67, 66, 34, 33, 0)] == [
        "Fresh", "Fresh", "Ready", "Ready", "Ease off", "Ease off", "Rest", "Rest"]
