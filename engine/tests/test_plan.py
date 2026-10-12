from datetime import date, datetime, timedelta

import pytest

from talus import api, cli, coach, ledger
from talus import plan as planner
from talus.route import route_numbers
from tests.conftest import sign_in
from tests.test_api import _gpx
from tests.test_coach import FakeClient, _keys

TODAY = date(2026, 10, 9)  # a Friday


def _acts(weeks=16, miles=(4, 12), gain=(400, 2400), today=TODAY):
    """A steady athlete: easy days Tue, Wed, Thu and Sun, a long day on Saturday."""
    out = []
    for k in range(weeks * 7, 0, -1):
        d = today - timedelta(days=k)
        if d.weekday() in (0, 4):
            continue
        m, g = (miles[1], gain[1]) if d.weekday() == 5 else (miles[0], gain[0])
        eff = m + g / 528
        out.append({"day": d.isoformat(), "start_time": d.isoformat() + "T08:00:00", "sport": "Run",
                    "distance_mi": m, "gain_ft": g, "loss_ft": g, "effort_mi": eff,
                    "moving_time_s": eff * 13 * 60, "zone_minutes": {"z1": eff * 11, "z2": eff * 2, "z3": 0.0},
                    "cardio_load": eff * 13 / 60 * 65, "climb_load": g * 1.0, "descent_load": g * 0.95,
                    "longest_descent_ft": g / 2, "minutes_above_8000ft": 0.0})
    return out


def _history(acts, today=TODAY):
    days = ledger.build([(date.fromisoformat(a["day"]), {"cardio": a["cardio_load"], "climb": a["climb_load"],
                                                         "descent": a["descent_load"]}) for a in acts], today)
    return [d for d in days if d.day < today]


def _obj(weeks=14, **kw):
    return planner.Objective(**({"name": "Test 50k", "day": TODAY + timedelta(weeks=weeks), "distance_mi": 31,
                                 "gain_ft": 7000, "loss_ft": 7000, "longest_descent_ft": 3000,
                                 "high_point_ft": 9000} | kw))


def test_readiness_reads_ready_close_and_gap_with_a_limiter():
    dem = {"dims": {"big_day": 40, "time_on_feet": 10, "climbing_volume": 5000, "descent_durability": 2000,
                    "descent_volume": 5000, "altitude": 9000}}
    cap = {"big_day": 28, "time_on_feet": 4.5, "climbing_volume": 4000, "descent_durability": 1500,
           "descent_volume": 7000, "altitude": 3}
    r = planner.evaluate(dem, cap)
    status = {d["key"]: d["status"] for d in r["dims"]}
    # 70% of 40 is ready; 45% against a 60% rule is within 20 points; 80% against 125% is not.
    assert status == {"big_day": "ready", "time_on_feet": "close", "climbing_volume": "gap",
                      "descent_durability": "ready", "descent_volume": "ready", "altitude": "context"}
    assert r["limiter"] == "climbing_volume" and r["verdict"] == "gap"
    dem["dims"]["descent_durability"] = None  # not given for a hand-entered route
    assert planner.evaluate(dem, cap)["dims"][3]["status"] == "unknown"


def test_week_kinds_keep_the_four_weeks_before_the_taper_for_building():
    assert planner.week_kinds(1) == ["race"]
    assert planner.week_kinds(5) == ["build"] * 4 + ["race"]
    kinds = planner.week_kinds(14)
    assert kinds[-2:] == ["taper", "race"] and kinds[-6:-2] == ["build"] * 4
    assert kinds.count("recovery") == 2 and kinds[0] == "build"
    for i, k in enumerate(kinds):  # never two recovery weeks in a row, never more than three builds
        if k == "recovery":
            assert kinds[i - 1] == "build"


def test_the_plan_builds_inside_the_limits_and_tapers():
    acts = _acts()
    p = planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY)
    weeks = p["weeks"]
    assert weeks[0]["start"] == "2026-10-05" and weeks[-1]["focus"] == "race"
    assert [w["focus"] for w in weeks].count("recovery") == 2
    builds = [w for w in weeks[1:] if w["focus"] in ("build", "peak")]
    level = weeks[0]
    for w in weeks[1:]:
        if w["focus"] in ("build", "peak"):
            for k in ("miles", "gain_ft", "loss_ft"):
                assert w[k] <= level[k] * planner.BUILD_RATE + 1, (w["start"], k)
            level = w
    peak = max(builds, key=lambda w: w["gain_ft"])
    assert peak["gain_ft"] > weeks[0]["gain_ft"] * 1.6
    assert weeks[-2]["gain_ft"] < peak["gain_ft"] and weeks[-1]["gain_ft"] < weeks[-2]["gain_ft"]
    # The objective is the race week's own day, not part of its training targets.
    assert weeks[-1]["miles"] < 31
    # Replaying the plan's weeks through the ledgers trips no limit the builder checks.
    proj = planner._Projector(_history(acts))
    rates = p["rates"]
    for w in weeks[1:-1]:
        start = date.fromisoformat(w["start"])
        sessions, _ = planner.sessions_for_week(w, start, start, planner.Constraints(), p["pace"]["min_per_effort_mi"])
        hist, bad = proj.trial([(date.fromisoformat(s["day"]),
                                 {"cardio": s["minutes"] / 60 * rates["cardio_per_hour"],
                                  "climb": s["gain_ft"] * rates["climb_per_ft"],
                                  "descent": s["loss_ft"] * rates["descent_per_ft"]}) for s in sessions])
        assert not bad, w["start"]
        proj.commit(hist)
    big_day = next(d for d in p["projected"]["dims"] if d["key"] == "big_day")
    assert big_day["capacity"] > 16.5  # the long days grow past today's biggest day


def test_time_limits_hold_every_day():
    acts = _acts()
    c = planner.Constraints(days=[1, 3, 5, 6], weekday_min=60, weekend_min=180, long_day=6)
    p = planner.build(_obj(), acts, _history(acts), c, TODAY)
    for w in p["weeks"]:
        assert w["long"] is None or w["long"]["minutes"] <= 180 + 1e-6
        assert w["hours"] <= (60 * 2 + 180 * 2) / 60 + 3  # week 0 also holds what's already done
    s = planner.sessions_for_week({"miles": 60, "gain_ft": 9000, "loss_ft": 9000}, date(2026, 10, 12),
                                  date(2026, 10, 12), c, 13.0)[0]
    assert all(x["minutes"] <= c.cap(date.fromisoformat(x["day"])) + 1e-6 for x in s)
    assert {x["kind"] for x in s if date.fromisoformat(x["day"]).weekday() not in c.days} == {"rest"}
    assert next(x for x in s if x["kind"] == "long")["day"] == "2026-10-18"


def test_a_loaded_athlete_starts_with_a_cut_week():
    acts = _acts()
    # Two huge weeks just before today: fatigue far above normal.
    for k in range(1, 12):
        d = (TODAY - timedelta(days=k)).isoformat()
        acts.append(acts[-1] | {"day": d, "start_time": d + "T17:00:00", "distance_mi": 15, "gain_ft": 5000,
                                "loss_ft": 5000, "effort_mi": 24.5, "moving_time_s": 24.5 * 13 * 60,
                                "cardio_load": 350, "climb_load": 5000, "descent_load": 4750})
    hist = _history(acts)
    assert any("ramp" in d.flags or "overreach" in d.flags for d in hist if d.day == TODAY - timedelta(days=1))
    p = planner.build(_obj(), acts, hist, planner.Constraints(), TODAY)
    first = p["weeks"][1]
    assert first["limited_by"] and "load limit" in first["notes"][0]


def test_out_of_reach_offers_a_smaller_objective_and_a_later_date():
    acts = _acts()
    obj = _obj(weeks=5, distance_mi=50, gain_ft=12000, loss_ft=12000)
    p = planner.build(obj, acts, _history(acts), planner.Constraints(), TODAY)
    assert not p["reachable"]
    opts = planner.options(obj, acts, _history(acts), planner.Constraints(), TODAY, p)
    assert 0.1 <= opts["smaller"]["factor"] < 1 and opts["smaller"]["gain_ft"] < 12000
    assert opts["later"] is None or opts["later"] > obj.day.isoformat()
    easy = _obj(weeks=10, distance_mi=10, gain_ft=1500, loss_ft=1500, longest_descent_ft=800)
    q = planner.build(easy, acts, _history(acts), planner.Constraints(), TODAY)
    assert q["reachable"]
    assert planner.options(easy, acts, _history(acts), planner.Constraints(), TODAY, q) == {"smaller": None, "later": None}


def test_upcoming_sessions_take_off_what_is_done_and_mark_the_objective():
    acts = _acts()
    obj = _obj(weeks=1)  # next Friday
    p = planner.build(obj, acts, _history(acts), planner.Constraints(), TODAY)
    s = planner.upcoming_sessions(p, acts, TODAY, days=10)  # stops at the objective
    assert [x["day"] for x in s] == [(TODAY + timedelta(days=k)).isoformat() for k in range(8)]
    assert s[-1]["kind"] == "objective" and s[-1]["miles"] == 31
    assert not any(x["kind"] == "long" for x in s if x["day"] >= "2026-10-12")  # race week: easy days only
    # A run today means today is done and the rest of the week is cut from what's left.
    ran = acts + [acts[-1] | {"day": TODAY.isoformat(), "start_time": TODAY.isoformat() + "T07:00:00"}]
    s2 = planner.upcoming_sessions(p, ran, TODAY)
    assert s2[0]["kind"] == "done"
    text = planner.session_text(s[1], 150)
    assert "150 bpm" in text["detail"] and "ft up" in text["detail"]


def test_plan_sessions_work_around_own_runs():
    acts = _acts()
    p = planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY)
    week = planner.plan_for_day(p, date(2026, 10, 12))
    nxt = lambda ss: [x for x in ss if x["day"] >= "2026-10-12"]  # noqa: E731

    def total(ss):
        return {k: sum(x[k] for x in ss) for k in planner.DIMS}

    # An easy run on the long day: it counts toward the week and the long day moves to Sunday.
    own = {"2026-10-17": {"kind": "easy", "miles": 4.0, "gain_ft": 300.0, "loss_ft": 300.0, "minutes": None}}
    s = nxt(planner.upcoming_sessions(p, acts, TODAY, days=10, own=own))
    sat = next(x for x in s if x["day"] == "2026-10-17")
    assert sat["own"] and sat["miles"] == 4.0
    assert next(x for x in s if x["kind"] == "long")["day"] == "2026-10-18"
    for k, v in total(s).items():
        assert v == pytest.approx(week[k], rel=0.02)
    # A big run of their own is the week's long day: no second one, and the week still adds up.
    own = {"2026-10-18": {"kind": "long", "miles": week["long"]["miles"] + 2, "gain_ft": week["long"]["gain_ft"],
                          "loss_ft": week["long"]["loss_ft"], "minutes": None}}
    s = nxt(planner.upcoming_sessions(p, acts, TODAY, days=10, own=own))
    assert [x["day"] for x in s if x["kind"] == "long"] == ["2026-10-18"]
    for k, v in total(s).items():
        assert v == pytest.approx(week[k], rel=0.02)
    # Own runs that already cover the week leave the plan's days as rest.
    own = {"2026-10-13": {"kind": "long", "miles": week["miles"], "gain_ft": week["gain_ft"],
                          "loss_ft": week["loss_ft"], "minutes": None}}
    s = nxt(planner.upcoming_sessions(p, acts, TODAY, days=10, own=own))
    assert {x["kind"] for x in s if not x.get("own")} == {"rest"}


def test_long_objectives_train_with_back_to_back_long_days():
    acts = _acts()
    p = planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY)  # a 50k of about 9.6 h
    assert p["back_to_back"]
    builds = [w for w in p["weeks"] if w["focus"] in ("build", "peak")]
    for w in builds:
        first, second = w["long"], w["long"]["second"]
        assert second and second["day"] == (date.fromisoformat(first["day"]) + timedelta(days=1)).isoformat()
        assert second["miles"] < first["miles"] and second["miles"] >= planner.B2B_MIN * first["miles"]
    assert p["weeks"][-1]["long"] is None  # the objective's week has no long days
    # The pair counts toward readiness, so the projection reads further than one capped day could.
    single = planner.build(_obj(), acts, _history(acts), planner.Constraints(back_to_back=False), TODAY)
    assert not single["back_to_back"] and all(w["long"] is None or w["long"]["second"] is None for w in single["weeks"])
    big = lambda plan: next(d for d in plan["projected"]["dims"] if d["key"] == "big_day")  # noqa: E731
    assert big(p)["capacity"] > big(single)["capacity"]
    assert "back-to-back" in big(p)["capacity_label"]
    # A short objective keeps one long day.
    short = planner.build(_obj(distance_mi=13, gain_ft=3000, loss_ft=3000), acts, _history(acts),
                          planner.Constraints(), TODAY)
    assert not short["back_to_back"]
    # The second day reads as its own session.
    sessions = planner.upcoming_sessions(p, acts, TODAY, days=10)
    assert [s["kind"] for s in sessions if s["day"] in ("2026-10-17", "2026-10-18")] == ["long", "long2"]
    assert planner.session_text(sessions[-1], 150)["title"] == "Back-to-back day"


def test_a_pair_of_days_counts_for_readiness_only_when_both_are_long():
    acts = _acts(weeks=10)
    base = planner.capacity(acts, 1000, TODAY)
    paired = planner.capacity(acts, 1000, TODAY, pairs=True)
    # Saturday's long day and Sunday's easy run: Sunday is under a third of Saturday, so no pair.
    assert paired["big_day"] == base["big_day"]
    d = "2026-10-04"  # make that Sunday big
    for a in acts:
        if a["day"] == d:
            a.update(effort_mi=10.0, moving_time_s=10 * 13 * 60)
    paired = planner.capacity(acts, 1000, TODAY, pairs=True)
    sat = max(a["effort_mi"] for a in acts if a["day"] == "2026-10-03")
    assert paired["big_day"] == pytest.approx((sat + 10) * planner.B2B_CREDIT)
    assert planner.capacity(acts, 1000, TODAY)["big_day"] == base["big_day"]


def test_missed_runs_are_not_piled_onto_the_days_left():
    acts = _acts()
    p = planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY)
    week = planner.plan_for_day(p, date(2026, 10, 12))
    full, _ = planner.sessions_for_week(week, date(2026, 10, 12), date(2026, 10, 12), planner.Constraints(),
                                        p["pace"]["min_per_effort_mi"], b2b=True)
    size = {s["kind"]: s for s in full}
    # Thursday, with nothing run Monday to Wednesday: the days left stay near their planned size.
    ahead = planner.upcoming_sessions(p, acts, date(2026, 10, 15), days=4)
    for s in ahead:
        if s["kind"] in size:
            for k in planner.DIMS:
                assert s[k] <= size[s["kind"]][k] * planner.MAKEUP + 1e-6, (s["day"], k)
    assert sum(s["miles"] for s in ahead) < week["miles"] * 0.9  # the week comes in under target


def test_speed_work_is_one_session_a_week_away_from_the_long_days():
    acts = _acts()
    c = planner.Constraints(workout="track")
    p = planner.build(_obj(), acts, _history(acts), c, TODAY)
    # Week 0 has only the weekend left, which the long days take.
    assert all(w["workout"] == "track" for w in p["weeks"][1:] if w["focus"] in ("build", "peak", "taper"))
    assert all(w["workout"] is None for w in p["weeks"] if w["focus"] in ("recovery", "race"))
    start = date(2026, 10, 12)
    week = planner.plan_for_day(p, start)
    ss, _ = planner.sessions_for_week(week, start, start, c, 13.0, b2b=True, workout="track")
    track = [s for s in ss if s["kind"] == "track"]
    assert len(track) == 1 and track[0]["gain_ft"] == 0  # flat
    day = date.fromisoformat(track[0]["day"])
    hard = [date.fromisoformat(s["day"]) for s in ss if s["kind"] in ("long", "long2", "hills")]
    assert min(abs((day - h).days) for h in hard) >= 2
    text = planner.session_text(track[0] | {"focus": "peak"}, 145, None, 168)
    assert text["title"] == "Track intervals" and "8 × 800 m" in text["how"] and "168 bpm" in text["how"]
    hill = planner.sessions_for_week(week, start, start, c, 13.0, workout="hill_repeats")[0]
    assert [s["kind"] for s in hill].count("hill_repeats") == 1 and "hills" not in [s["kind"] for s in hill]
    # The dated sessions carry it, and the calendar names it as intervals.
    assert "track" in [s["kind"] for s in planner.upcoming_sessions(p, acts, date(2026, 10, 12), days=7)]


def test_a_fit_athlete_holds_near_what_the_objective_needs():
    acts = _acts(miles=(12, 24), gain=(2800, 6000))  # about 72 mi and 17,200 ft a week
    c = planner.Constraints(days=[0, 1, 2, 3, 5, 6], weekday_min=120, weekend_min=480)
    p = planner.build(_obj(gain_ft=8000, loss_ft=8000, weeks=12), acts, _history(acts), c, TODAY)
    assert p["peak"]["miles"] < p["baseline"]["miles"] * 0.9
    assert all(w["miles"] <= p["peak"]["miles"] + 0.5 for w in p["weeks"][1:])
    biggest = max(w["long"]["miles"] for w in p["weeks"] if w["long"])
    assert biggest < 24  # not a long day near the objective's 31 mi
    from talus import interview
    assert "doesn't build" in interview.draft_summary(p, "Baldy", 5, {})


def test_weekly_review_flags_a_missed_week_and_pain():
    acts = _acts()
    p = planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY - timedelta(days=11))
    start = date(2026, 9, 28)
    light = [a for a in acts if not (start.isoformat() <= a["day"] <= "2026-10-04")]
    light.append(_acts()[-1] | {"day": "2026-09-30", "distance_mi": 3, "gain_ft": 100, "loss_ft": 100})
    r = planner.weekly_review(start, light, p, _history(acts), [2, 4])
    assert r["target"] is not None and r["share"]["miles"] < 0.7
    assert r["replan"] == ["missed", "pain"]
    ok = planner.weekly_review(start, acts, p, _history(acts), [1])
    assert ok["replan"] == [] and ok["actual"]["activities"] == 5
    assert ok["actual"]["below_aet_pct"] == pytest.approx(11 / 13)


def test_diff_names_the_weeks_that_moved():
    acts = _acts()
    a = planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY)
    b = planner.build(_obj(distance_mi=15, gain_ft=3000, loss_ft=3000), acts, _history(acts), planner.Constraints(), TODAY)
    assert planner.diff(None, b) == []
    changes = planner.diff(a, b)
    assert changes and all(c["before"] is not None for c in changes)
    assert planner.diff(a, a) == []


def test_route_numbers_from_a_planned_route_without_times():
    pts = []
    for i in range(2001):  # about 6.2 mi over a summit 3,000 ft up
        up = i if i <= 1000 else 2000 - i
        pts.append(f'<rtept lat="{37 + i * 0.000045:.6f}" lon="-118.0"><ele>{2000 + up * 0.9144:.1f}</ele></rtept>')
    gpx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><rte><name>Peak</name>' + "".join(pts)
           + "</rte></gpx>").encode()
    r = route_numbers(gpx)
    assert r["name"] == "Peak"
    assert 6 < r["distance_mi"] < 6.5
    assert abs(r["gain_ft"] - 3000) < 100 and abs(r["loss_ft"] - 3000) < 100
    assert abs(r["longest_descent_ft"] - 3000) < 100 and abs(r["high_point_ft"] - (2000 / 0.3048 + 3000)) < 50
    with pytest.raises(ValueError):
        route_numbers(b'<gpx xmlns="http://www.topografix.com/GPX/1/1"></gpx>')


# API ---------------------------------------------------------------------------------------

def _setup(tmp_path, monkeypatch, days=42):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in range(days, 0, -1):
        if k % 7 in (1, 4):
            continue
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:02d}.gpx"
        f.write_bytes(_gpx(d, n=2400, climb_per_s=0.3))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", "--no-coach", *files])
    return sign_in()


OBJ = {"name": "Fall 30k", "distance_mi": 18.6, "gain_ft": 4000, "loss_ft": 4000, "longest_descent_ft": 1800,
       "high_point_ft": 7500}


def test_objective_plan_accept_and_today(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    empty = client.get("/api/plan").json()
    assert empty["objective"] is None and empty["constraints"]["long_day"] == 5
    day = (date.today() + timedelta(weeks=10)).isoformat()
    r = client.put("/api/plan/objective", json=OBJ | {"day": day})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["objective"]["name"] == "Fall 30k" and p["objective"]["days_to_go"] == 70
    assert {d["key"] for d in p["readiness"]["dims"]} >= {"big_day", "climbing_volume", "descent_volume"}
    assert p["draft"] and p["draft"]["reason"] == "new" and p["accepted"] is None and p["sessions"] == []
    assert p["draft"]["weeks"][-1]["focus"] == "race"

    r = client.post(f"/api/plan/{p['draft']['id']}/accept").json()
    assert r["accepted"]["id"] == p["draft"]["id"] and r["draft"] is None
    assert len(r["sessions"]) == 7 and all("title" in s and "detail" in s for s in r["sessions"])
    assert r["review"] is not None and "replan_text" in r["review"]
    t = client.get("/api/today").json()
    today_s = r["sessions"][0]
    if t["call"]["call"] == "go" and today_s["kind"] != "done":
        assert t["call"]["planned"] and t["call"]["session"] == today_s["title"]

    # Editing the objective drafts a new plan shown against the accepted one.
    r = client.put("/api/plan/objective", json=OBJ | {"day": day, "gain_ft": 6000, "loss_ft": 6000}).json()
    assert r["draft"]["reason"] == "objective" and r["draft"]["changes"]
    assert r["accepted"]["id"] == p["draft"]["id"]
    r = client.post(f"/api/plan/{r['draft']['id']}/decline").json()
    assert r["draft"] is None and r["accepted"]["id"] == p["draft"]["id"]
    assert client.post("/api/plan/999/accept").status_code == 404

    # Constraints: the long day must be a training day; saving drafts a new plan.
    bad = {"days": [1, 3, 6], "weekday_min": 60, "weekend_min": 240, "long_day": 5}
    assert client.put("/api/plan/constraints", json=bad).status_code == 422
    r = client.put("/api/plan/constraints", json=bad | {"long_day": 6}).json()
    assert r["constraints"]["days"] == [1, 3, 6] and r["draft"]["reason"] == "constraints"
    assert all(date.fromisoformat(w["long"]["day"]).weekday() == 6 for w in r["draft"]["weeks"] if w["long"])

    # A past date is refused; a new objective starts over.
    assert client.put("/api/plan/objective", json=OBJ | {"day": date.today().isoformat()}).status_code == 422
    r = client.put("/api/plan/objective", json=OBJ | {"day": day, "name": "Other", "new": True}).json()
    assert r["accepted"] is None and r["draft"]["reason"] == "new"
    assert client.delete("/api/plan/objective").json() == {"ok": True}
    assert client.get("/api/plan").json()["objective"] is None


def test_plans_belong_to_their_account(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    day = (date.today() + timedelta(weeks=8)).isoformat()
    assert client.put("/api/plan/objective", json=OBJ | {"day": day}).status_code == 200
    other = sign_in("second@example.com", "another long password")
    assert other.get("/api/plan").json()["objective"] is None
    # No training synced on the new account: the objective is kept, the plan waits for data.
    r = other.put("/api/plan/objective", json=OBJ | {"day": day})
    assert r.status_code == 200 and r.json()["draft"] is None
    assert other.post("/api/plan/draft", json={}).status_code == 409
    assert client.get("/api/plan").json()["objective"]["name"] == "Fall 30k"


def test_plan_coach_explains_without_names_and_checks_numbers(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    day = (date.today() + timedelta(weeks=9)).isoformat()
    client.put("/api/plan/objective", json=OBJ | {"day": day})
    r = client.get("/api/coach/plan").json()
    assert r["source"] == "rules" and r["fallback_reason"] == "no_key" and set(r["reply"]) == set(coach.PLAN_FIELDS)
    store = api.get_store()
    p = store.plan("draft")
    packet = coach.plan_packet(p, p["now"], date.today(), "New objective", [])
    assert "name" not in _keys(packet) and "Fall 30k" not in str(packet)
    gain = packet["objective"]["gain_ft"]
    good = {"verdict": f"The route climbs {gain:,} ft.", "limiter": "Descent volume.", "plan": "Build, then taper."}
    bad = dict(good, plan="Run 123,456 ft a week.")
    fake = FakeClient(bad, good)
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    r = client.get("/api/coach/plan").json()
    assert r["source"] == "coach" and r["reply"] == good and len(fake.calls) == 2
    assert "Mode: plan." in fake.calls[0]["messages"][0]["content"]
    assert fake.calls[0]["model"] == "claude-opus-5-5"
    assert client.get("/api/coach/plan").json()["log_id"] == r["log_id"]  # reused, not asked again
    client.post(f"/api/plan/{p['id']}/accept")
    fake.replies.append({"week": "Steady.", "next": "More of the same."})
    rv = client.get("/api/coach/review").json()
    assert rv["source"] == "coach" and rv["review"]["start"] <= date.today().isoformat()


def test_chat_answers_with_checked_numbers(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.post("/api/coach/chat", json={"question": "Can I run 20 mi on Saturday?"}).json()
    assert [m["role"] for m in r["messages"]] == ["athlete", "coach"]
    assert "isn't set up" in r["messages"][1]["text"] and r["fallback_reason"] == "no_key"
    fake = FakeClient({"answer": "20 mi is 99,999 ft.", "used": []},
                      {"answer": "20 mi would be a big day for you.", "used": ["last_14_days"]})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    r = client.post("/api/coach/chat", json={"question": "And 20 mi on Sunday?"}).json()
    assert r["source"] == "coach" and r["messages"][-1]["text"] == "20 mi would be a big day for you."
    packet = __import__("json").loads(fake.calls[0]["messages"][0]["content"].split("Data:\n", 1)[1])
    assert packet["question"] == "And 20 mi on Sunday?" and len(packet["conversation"]) == 2
    assert "Mode: chat." in fake.calls[0]["messages"][0]["content"]
    assert len(client.get("/api/coach/chat").json()["messages"]) == 4
    assert client.delete("/api/coach/chat").json() == {"ok": True}
    assert client.get("/api/coach/chat").json()["messages"] == []


def test_route_upload(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch, days=7)
    gpx = _gpx(datetime(2026, 1, 1, 8), n=1200, climb_per_s=0.5)
    r = client.post("/api/plan/route", content=gpx, headers={"content-type": "application/gpx+xml"})
    assert r.status_code == 200 and r.json()["gain_ft"] > 500
    assert client.post("/api/plan/route", content=b"nope").status_code == 422


# Scheduled runs ----------------------------------------------------------------------------

def test_schedule_add_move_delete_and_match(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    r = client.post("/api/schedule", json={"day": tomorrow, "kind": "long", "miles": 14, "gain_ft": 3500, "zone": 1})
    assert r.status_code == 200
    rid = r.json()["id"]
    cal = client.get("/api/schedule").json()
    it = next(i for i in cal["items"] if i["id"] == rid)
    assert it["source"] == "manual" and it["status"] == "planned" and it["loss_ft"] == 3500
    assert it["estimated_minutes"] and it["minutes"] > 60 and "zone 1" in it["detail"] and "14.0 mi" in it["detail"]
    # Move it to today: Today shows it, and on a go day it is the session.
    body = {"day": today.isoformat(), "kind": "long", "miles": 14, "gain_ft": 3500, "zone": 1}
    assert client.put(f"/api/schedule/{rid}", json=body).status_code == 200
    t = client.get("/api/today").json()
    assert t["planned"]["title"] == "Long run" and t["planned"]["source"] == "manual"
    assert t["call"]["planned"] == (t["call"]["call"] == "go")
    if t["call"]["call"] != "go":
        assert t["call"]["session"] != "Long run"  # an easier call is never overridden
    assert client.put("/api/schedule/999", json=body).status_code == 404
    assert client.post("/api/schedule", json=body | {"day": (today - timedelta(days=30)).isoformat()}).status_code == 422
    assert client.post("/api/schedule", json=body | {"kind": "swim"}).status_code == 422
    # A past day with an activity on it reads done; one without reads missed.
    ran = client.get("/api/activities").json()[0]["day"]
    client.post("/api/schedule", json=body | {"day": ran, "kind": "easy"})
    gap = next(d for k in range(1, 7) if (d := (today - timedelta(days=k)).isoformat()) not in
               {a["day"] for a in client.get("/api/activities").json()})
    client.post("/api/schedule", json=body | {"day": gap, "kind": "easy"})
    cal = client.get(f"/api/schedule?start={(today - timedelta(days=7)).isoformat()}&days=14").json()
    status = {i["day"]: i["status"] for i in cal["items"] if i["source"] == "manual"}
    assert status[gap] == "missed"
    if ran >= (today - timedelta(days=7)).isoformat():
        assert status[ran] == "done"
    assert client.delete(f"/api/schedule/{rid}").json() == {"ok": True}
    assert client.delete(f"/api/schedule/{rid}").status_code == 404
    other = sign_in("second@example.com", "another long password")
    assert [i for i in other.get("/api/schedule").json()["items"] if i["source"] == "manual"] == []


def test_scheduled_runs_project_on_load_and_flag_a_ramp(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    today = date.today()
    assert client.get("/api/load").json()["projection"]["series"] == {}
    for k in range(1, 15):  # two weeks of very big days
        d = (today + timedelta(days=k)).isoformat()
        client.post("/api/schedule", json={"day": d, "kind": "long", "miles": 20, "gain_ft": 6000})
    load = client.get("/api/load").json()
    proj = load["projection"]
    assert proj["until"] == (today + timedelta(days=14)).isoformat()
    assert proj["series"]["climb"][-1]["day"] == proj["until"]
    assert proj["series"]["climb"][-1]["fatigue"] > load["ledgers"]["climb"]["today"]["fatigue"]
    assert proj["warnings"] and proj["warnings"][0]["key"] in ("ramp", "overreach")
    assert client.get("/api/schedule").json()["warnings"] == proj["warnings"]


def test_own_run_replaces_the_plan_session_that_day(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    day = (date.today() + timedelta(weeks=10)).isoformat()
    p = client.put("/api/plan/objective", json=OBJ | {"day": day}).json()
    p = client.post(f"/api/plan/{p['draft']['id']}/accept").json()
    target = next(s for s in p["sessions"] if s["kind"] not in ("rest", "done"))
    client.post("/api/schedule", json={"day": target["day"], "kind": "hike", "miles": 6, "gain_ft": 2500,
                                       "notes": "Peak with friends"})
    p = client.get("/api/plan").json()
    s = next(x for x in p["sessions"] if x["day"] == target["day"])
    assert s["own"] and s["title"] == "Hike" and s["how"] == "Peak with friends"
    assert p["this_week"]["target"]["miles"] > 0 and p["this_week"]["done"] is not None
    assert all(a["miles"] >= 0 for a in p["actuals"].values()) and p["actuals"]
    sched = client.get("/api/schedule").json()
    assert sched["weeks"][0]["target"] and len(sched["weeks"]) == 5
    cal = sched["items"]
    on_day = [i for i in cal if i["day"] == target["day"]]
    assert [i["source"] for i in on_day] == ["manual"]
    assert any(i["source"] == "plan" for i in cal)


def test_match_prefers_the_same_type():
    items = [{"day": "2026-10-10", "kind": "hike"}, {"day": "2026-10-10", "kind": "easy"}]
    acts = [{"id": 1, "day": "2026-10-10", "sport": "Run", "start_time": "2026-10-10T07:00", "name": "r",
             "distance_mi": 5, "gain_ft": 300, "loss_ft": 300, "moving_time_s": 2700},
            {"id": 2, "day": "2026-10-10", "sport": "Hike", "start_time": "2026-10-10T12:00", "name": "h",
             "distance_mi": 6, "gain_ft": 2000, "loss_ft": 2000, "moving_time_s": 9000}]
    from talus import schedule
    schedule.match(items, acts)
    assert items[0]["activity"]["id"] == 2 and items[1]["activity"]["id"] == 1
