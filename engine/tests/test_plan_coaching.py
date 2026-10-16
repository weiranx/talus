from copy import deepcopy
from datetime import date, timedelta

from talus import api, coach, plan as planner, plan_coaching as coaching
from tests.test_plan import _acts, _history, _obj, TODAY, _setup, OBJ


def plan():
    acts = _acts()
    return planner.build(_obj(), acts, _history(acts), planner.Constraints(), TODAY), acts


def test_busy_preserves_key_work_when_possible_but_sore_eases_it():
    p, acts = plan()
    before = deepcopy(p)
    busy = coaching.revise(p, acts, TODAY, {}, "busy", TODAY + timedelta(days=1), {})
    sore = coaching.revise(p, acts, TODAY, {}, "sore", TODAY + timedelta(days=1), {})
    assert busy["changes"] and sore["changes"]
    assert all(c["after"]["kind"] in ("easy", "rest") for c in sore["changes"])
    assert p == before
    for w in busy["weeks"] + sore["weeks"]:
        assert all(w["after"][k] <= w["before"][k] + .01 for k in w["before"])


def test_broken_back_to_back_pair_does_not_leave_a_second_long_day():
    p, acts = plan()
    ss = planner.upcoming_sessions(p, acts, TODAY, 14)
    first = next(s for s in ss if s["kind"] == "long" and any(
        other["kind"] == "long2" and date.fromisoformat(other["day"]) == date.fromisoformat(s["day"]) + timedelta(days=1)
        for other in ss))
    result = coaching.revise(p, acts, TODAY, {}, "busy", date.fromisoformat(first["day"]), {})
    after = planner.upcoming_sessions(result["after_plan"], acts, TODAY, 14)
    by = {s["day"]: s for s in after}
    for s in after:
        if s["kind"] == "long2":
            prior = (date.fromisoformat(s["day"]) - timedelta(days=1)).isoformat()
            assert by[prior]["kind"] == "long"


def test_date_caps_and_flat_terrain_apply_to_allocator_and_revised_sessions():
    p, acts = plan()
    d = TODAY + timedelta(days=1)
    limits = {d.isoformat(): {"minutes": 30, "terrain": "flat", "locked": False}}
    result = coaching.revise(p, acts, TODAY, {}, "availability", d, limits)
    sessions = planner.upcoming_sessions(result["after_plan"], acts, TODAY, 14)
    s = next(s for s in sessions if s["day"] == d.isoformat())
    assert s["minutes"] <= 30 and s["gain_ft"] == s["loss_ft"] == 0
    c = planner.Constraints(date_limits=limits)
    ss, _ = planner.sessions_for_week({"miles": 30, "gain_ft": 5000, "loss_ft": 5000},
                                     d - timedelta(days=d.weekday()), TODAY, c, 15)
    s = next(s for s in ss if s["day"] == d.isoformat())
    assert s["minutes"] <= 30 and s["gain_ft"] == s["loss_ft"] == 0


def test_locked_and_completed_sessions_cannot_be_changed():
    import pytest
    p, acts = plan()
    d = TODAY + timedelta(days=1)
    with pytest.raises(ValueError, match="fixed"):
        coaching.revise(p, acts, TODAY, {}, "busy", d,
                         {d.isoformat(): {"minutes": 360, "locked": True}})
    s = next(s for s in planner.upcoming_sessions(p, acts, TODAY, 14) if s["day"] == d.isoformat())
    with pytest.raises(ValueError, match="fixed"):
        coaching.revise(p, acts, TODAY, {s["day"]: s}, "sore", d, {})


def setup_plan(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    obj = OBJ | {"day": (date.today() + timedelta(weeks=12)).isoformat()}
    response = client.put("/api/plan/objective", json=obj)
    assert response.status_code == 200, response.text
    draft = response.json()["draft"]
    assert client.post(f"/api/plan/{draft['id']}/accept").status_code == 200
    return client


def test_preview_accept_exact_sessions_undo_and_stale_conflict(tmp_path, monkeypatch):
    client = setup_plan(tmp_path, monkeypatch)
    state = client.get("/api/plan/coaching").json()
    d = next(s["day"] for s in state["sessions"] if s["kind"] not in ("rest", "done", "objective"))
    body = {"reason": "availability", "day": d, "availability": {d: {"minutes": 0}}}
    response = client.post("/api/plan/revisions", json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert client.get("/api/plan/coaching").json()["sessions"] == state["sessions"]
    assert client.post(f"/api/plan/revisions/{preview['id']}/accept").status_code == 200
    after = client.get("/api/plan/coaching").json()
    for c in preview["changes"]:
        s = next(s for s in after["sessions"] if s["day"] == c["day"])
        assert coaching.session(s) == c["after"]
    assert client.post(f"/api/plan/revisions/{preview['id']}/undo").status_code == 200
    assert client.get("/api/plan/coaching").json()["sessions"] == state["sessions"]
    preview = client.post("/api/plan/revisions", json=body).json()
    client.post("/api/checkin", json={"soreness": 2, "energy": 3, "pain": 1})
    assert client.post(f"/api/plan/revisions/{preview['id']}/accept").status_code == 409


def test_outcomes_preserve_intent_and_feed_review_and_evidence(tmp_path, monkeypatch):
    client = setup_plan(tmp_path, monkeypatch)
    today = date.today().isoformat()
    # Today has no imported workout; completed intent requires a synced activity.
    assert client.put("/api/plan/outcomes", json={"day": today, "intent": "met", "effort": 6}).status_code == 409
    assert client.put("/api/plan/outcomes", json={"day": today, "intent": "missed", "effort": 6}).status_code == 422
    state = client.get("/api/plan/coaching").json()
    assert state["evidence"]["assumptions"] and state["evidence"]["activity_days"] > 0
    assert client.post("/api/plan/revisions", json={"reason": "busy", "day": "2020-01-01"}).status_code == 422


def test_evidence_never_invents_probability_or_missing_activity_values():
    p, acts = plan()
    p["pace"]["learned"] = False
    e = coaching.evidence(p, [], TODAY, [], [])
    assert e["label"] == "Limited evidence" and e["last_activity"] is None and e["milestone"] is None
    assert len(e["missing"]) >= 3
    assert coaching.outcomes_summary([])["effort_average"] is None


def test_reported_workout_intent_survives_edits_and_reaches_weekly_coach(tmp_path, monkeypatch):
    client = setup_plan(tmp_path, monkeypatch)
    store = api.get_store()
    today = date.today()
    first = today - timedelta(days=14)
    acts = api._activities(store)
    p = planner.build(store.objective(), acts, [d for d in api._plan_history(store, first) if d.day < first],
                      planner.Constraints(), first)
    p = api._draft_extras(store, store.objective(), acts, api._plan_history(store, today), planner.Constraints(), today, p)
    pid = store.save_draft(store.objective().id, "request", p)
    store.decide_draft(pid, True)
    data = client.get("/api/plan/coaching").json()
    monday = (today - timedelta(days=today.weekday())).isoformat()
    last_monday = (today - timedelta(days=today.weekday() + 7)).isoformat()
    recent = next(s for s in data["recent_sessions"] if s["completed"] and last_monday <= s["day"] < monday)
    body = {"day": recent["day"], "intent": "partial", "effort": 8, "reason": "too_hard"}
    response = client.put("/api/plan/outcomes", json=body)
    assert response.status_code == 200, response.text
    snapshot = response.json()["planned"]
    response = client.put("/api/plan/outcomes", json=body | {"effort": 7})
    assert response.json()["planned"] == snapshot
    review = client.get("/api/plan").json()["review"]
    assert review["outcomes"]["partial"] == 1 and review["outcomes"]["effort_average"] == 7
    packet = coach.review_packet(review, None, None, [])
    assert packet["workout_outcomes"]["sessions"][0]["planned"] == snapshot
    assert "reported" in coach.rules_review(packet)["week"]
    assert client.get("/api/coach/plan").status_code == 200


def test_future_locks_caps_and_pain_changes_keep_their_contract():
    import pytest
    p, acts = plan()
    future = TODAY + timedelta(days=22)
    limits = {future.isoformat(): {"minutes": 360, "terrain": "any", "locked": True}}
    change = coaching.revise(p, acts, TODAY, {}, "availability", TODAY, limits)
    assert future.isoformat() in change["after_plan"]["session_overrides"]
    d = TODAY + timedelta(days=1)
    result = coaching.revise(p, acts, TODAY, {}, "busy", d, {}, pain=True)
    assert all(c["after"]["kind"] == "rest" for c in result["changes"])
    with pytest.raises(ValueError):
        coaching.revise(p, acts, TODAY, {}, "availability", TODAY,
                         {future.isoformat(): {"minutes": 1, "terrain": "flat", "locked": True}})


def test_undo_cannot_overwrite_later_calendar_edits_and_other_accounts_cannot_accept(tmp_path, monkeypatch):
    from tests.conftest import sign_in
    client = setup_plan(tmp_path, monkeypatch)
    data = client.get("/api/plan/coaching").json()
    d = next(s["day"] for s in data["sessions"] if s["kind"] not in ("rest", "done", "objective"))
    r = client.post("/api/plan/revisions", json={"reason": "too_hard", "day": d})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    other = sign_in("other-coach@example.com", "synthetic test password")
    assert other.post(f"/api/plan/revisions/{rid}/accept").status_code == 404
    assert client.post(f"/api/plan/revisions/{rid}/accept").status_code == 200
    assert client.post("/api/schedule", json={"day": d, "kind": "easy", "miles": 2}).status_code == 200
    assert client.post(f"/api/plan/revisions/{rid}/undo").status_code == 409
