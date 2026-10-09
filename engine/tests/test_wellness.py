from datetime import date, timedelta

import httpx

from talus import api, coach, wellness
from talus.ledger import LedgerDay
from talus.readiness import daily_call, readiness
from talus.sources.base import WellnessDay
from talus.sources.intervals import IntervalsSource
from talus.store import Store
from tests.conftest import sign_in

TODAY = date(2026, 10, 9)


def history(hrv=60.0, rhr=48.0, sleep_h=8.0, nights=60, last=None):
    """Steady nights, with the most recent ones overridden: last={"hrv": [52, 50], ...} sets
    today, then yesterday, and so on."""
    days = {}
    for k in range(nights):
        days[TODAY - timedelta(days=k)] = {"hrv": hrv, "resting_hr": rhr, "sleep_s": sleep_h * 3600}
    for key, values in (last or {}).items():
        for k, v in enumerate(values):
            d = days.setdefault(TODAY - timedelta(days=k), {})
            d[key] = v
    return days


def fresh_loads():
    return {n: LedgerDay(TODAY, n, 0, 100, 100, []) for n in ("cardio", "climb", "descent")}


def test_quiet_signals_change_nothing():
    w = wellness.evaluate(history(), TODAY, need_h=8)
    assert [w[k]["status"] for k in ("hrv", "resting_hr", "sleep")] == ["ok", "ok", "ok"]
    assert wellness.engine_effect(w) is None
    assert readiness(fresh_loads(), None, w) == readiness(fresh_loads(), None)


def test_hrv_reads_the_7_day_average_not_one_night():
    one_bad_night = wellness.evaluate(history(last={"hrv": [40]}), TODAY, need_h=8)
    # 6 nights at 60 and one at 40 average 57.1, about 5% under a 59.7 baseline: still fine.
    assert one_bad_night["hrv"]["status"] == "ok"
    week_down = wellness.evaluate(history(last={"hrv": [52] * 7}), TODAY, need_h=8)
    assert week_down["hrv"]["status"] == "low"
    assert week_down["hrv"]["text"] == "7-day average 12% below your 60-day baseline"
    r = readiness(fresh_loads(), None, week_down)
    assert r["engine"]["state"] == "loaded" and r["legs"]["state"] == "fresh"
    assert r["engine"]["reason"] == "overnight HRV is 12% below your 60-day baseline"


def test_hrv_down_and_resting_hr_up_together_read_as_overreached():
    w = wellness.evaluate(history(last={"hrv": [50] * 7, "resting_hr": [55]}), TODAY, need_h=8)
    assert w["resting_hr"]["status"] == "high"
    r = readiness(fresh_loads(), None, w)
    assert r["engine"]["state"] == "overreached"
    call = daily_call(r, None, 150)
    assert call["call"] == "rest" and "resting HR is 7 bpm above" in call["why"]


def test_short_sleep_last_night_or_this_week():
    night = wellness.evaluate(history(last={"sleep_s": [5.5 * 3600]}), TODAY, need_h=8)
    assert night["sleep"]["status"] == "short"
    assert wellness.engine_effect(night) == ("loaded", "you slept 5 h 30 min last night")
    week = wellness.evaluate(history(last={"sleep_s": [6.9 * 3600] * 7}), TODAY, need_h=8)
    assert week["sleep"]["status"] == "short" and week["sleep"]["short"] == "week"
    assert wellness.evaluate(history(last={"sleep_s": [7 * 3600]}), TODAY, need_h=8)["sleep"]["status"] == "ok"


def test_overnight_never_softens_load_or_checkin():
    now = fresh_loads()
    now["cardio"] = LedgerDay(TODAY, "cardio", 0, 100, 160, ["overreach"])
    w = wellness.evaluate(history(last={"hrv": [52] * 7}), TODAY, need_h=8)
    r = readiness(now, None, w)
    assert r["engine"]["state"] == "overreached" and r["engine"]["reason"].startswith("cardio load")


def test_missing_data_is_labeled_and_ignored():
    empty = wellness.evaluate({}, TODAY, need_h=8)
    assert {empty[k]["status"] for k in ("hrv", "resting_hr", "sleep")} == {"no_data"}
    assert empty["resting_hr"]["text"] == "No reading last night"
    assert readiness(fresh_loads(), None, empty) == readiness(fresh_loads(), None)
    # Watch not synced yet this morning: HRV still has its week, resting HR has nothing.
    days = history()
    del days[TODAY]
    w = wellness.evaluate(days, TODAY, need_h=8)
    assert w["hrv"]["status"] == "ok" and w["hrv"]["last_night"] is None
    assert w["resting_hr"]["status"] == "no_data"
    assert w["sleep"]["text"].startswith("No data last night, 8 h a night this week")


def test_short_history_builds_a_baseline_first():
    w = wellness.evaluate(history(nights=10, last={"hrv": [30] * 7, "resting_hr": [70]}), TODAY, need_h=8)
    assert w["hrv"]["status"] == "building" and w["resting_hr"]["status"] == "building"
    assert w["hrv"]["text"] == "Building your baseline: 10 of 14 nights"
    assert wellness.engine_effect(w) is None


def test_intervals_wellness_is_parsed_and_empty_days_dropped():
    rows = [{"id": "2026-10-08", "hrv": 58.0, "restingHR": 49, "sleepSecs": 26100, "sleepScore": 81},
            {"id": "2026-10-09", "hrv": None, "restingHR": 51, "sleepSecs": None},
            {"id": "2026-10-07", "hrv": None, "restingHR": None, "sleepSecs": 0, "weight": 70}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/athlete/i42/wellness"
        assert request.url.params["oldest"] == "2026-08-04"
        return httpx.Response(200, json=rows)

    client = httpx.Client(base_url="https://intervals.icu/api/v1", transport=httpx.MockTransport(handler))
    days = IntervalsSource(api_key="k", athlete_id="i42", client=client).list_wellness(date(2026, 8, 4), TODAY)
    assert [(d.day, d.hrv, d.resting_hr, d.sleep_s) for d in days] == [
        (date(2026, 10, 8), 58.0, 49, 26100), (date(2026, 10, 9), None, 51, None)]


def test_store_round_trip(tmp_path):
    store = Store(tmp_path)
    store.save_wellness([WellnessDay("intervals", TODAY, hrv=58, resting_hr=49, sleep_s=26100)])
    store.save_wellness([WellnessDay("intervals", TODAY, hrv=60, resting_hr=48, sleep_s=27000)])
    assert store.wellness(TODAY, TODAY) == {TODAY: {"hrv": 60, "resting_hr": 48, "sleep_s": 27000,
                                                    "sleep_score": None}}


def test_today_and_brief_carry_overnight_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TALUS_SLEEP_NEED_H", "8")
    monkeypatch.setattr(api, "coach_client", lambda: None)
    store = api.get_store()
    from talus.models import Thresholds
    store.set_thresholds(Thresholds(45, 150, 172, 190))
    client = sign_in()

    t = client.get("/api/today").json()
    assert t["wellness"]["hrv"]["status"] == "no_data" and t["readiness"]["engine"]["state"] == "fresh"
    before = coach.brief_packet(t, [])

    today = date.today()
    store.save_wellness([WellnessDay("intervals", today - timedelta(days=k), hrv=60 if k > 6 else 50,
                                     resting_hr=48, sleep_s=8 * 3600) for k in range(60)])
    t = client.get("/api/today").json()
    assert t["wellness"]["hrv"]["status"] == "low" and t["readiness"]["engine"]["state"] == "loaded"
    packet = coach.brief_packet(t, [])
    assert packet["overnight"]["hrv_ms"]["avg_7d"] == 50 and packet["overnight"]["sleep_hours"]["need"] == 8
    assert packet["overnight"]["hrv_ms"]["vs_baseline_pct"] == -15
    # New overnight data changes the packet, so the day's cached brief is not reused.
    assert coach.packet_hash(packet) != coach.packet_hash(before)
    assert coach.check({"call": "modify", "why": "Your HRV is 15% below baseline at 50 ms."}, packet, "brief") == []
