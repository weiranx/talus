from dataclasses import asdict
from datetime import date, timedelta

from talus import api, interview
from talus import plan as planner
from tests.test_coach import FakeClient
from tests.test_plan import OBJ, TODAY, _acts, _history, _obj, _setup


def test_objective_from_plain_words():
    o = interview.rules_objective("I'm training for the Baldy 50k on Jan 2, 8,000 ft up", TODAY)
    assert o == {"name": "Baldy 50k", "day": "2027-01-02", "distance_mi": 31.1, "gain_ft": 8000.0}
    o = interview.rules_objective("Grand Canyon R2R 24 mi 4,500' up 5,800 ft down 2026-11-14", TODAY)
    assert o["distance_mi"] == 24 and o["gain_ft"] == 4500 and o["loss_ft"] == 5800 and o["day"] == "2026-11-14"
    assert interview.parse_date("on 3/7", TODAY) == date(2027, 3, 7)  # the next one after today
    assert interview.missing({"name": "X", "distance_mi": 20}) == ["day", "gain_ft"]


def test_chip_answers_and_typed_answers():
    d = {"days": ["Tue", "Sat"], "long_day": "Sat", "weekday": "1 h 30", "weekend": "6 h"}
    assert interview.chip_answers("days", {"days": ["Mon", "Wed"], "long_day": "Sun"}, "", d)[0] == {
        "days": ["Mon", "Wed", "Sun"], "long_day": "Sun"}  # the long day is always a training day
    assert interview.chip_answers("days", {}, "weekdays except wed, long run sunday", d)[0] == {
        "days": ["Mon", "Tue", "Thu", "Fri", "Sun"], "long_day": "Sun"}
    assert interview.chip_answers("time", {}, "", d)[0] == {"weekday_min": 90, "weekend_min": 360}
    assert interview.chip_answers("time", {"weekday": "1 h", "weekend": "4 h"}, "", {})[0] == {
        "weekday_min": 60, "weekend_min": 240}
    assert interview.chip_answers("goal", {}, "sub 8 hours", d)[0]["goal"] == "A time goal"
    body = interview.chip_answers("body", {}, "left knee a bit sore and tired lately", d)[0]
    assert body["body"] == "A small niggle" and body["feel"] == "Tired lately"
    assert interview.chip_answers("goal", {"goal": "Fly"}, "", d)[1]  # not a chip


def test_edits_from_plain_words_change_the_constraints():
    edits = interview.rules_edits("Move my long day to Sunday, I can't run Tuesdays, and less vert in week 3")
    assert {"kind": "long_day", "day": 6} in edits and {"kind": "drop_day", "day": 1} in edits
    assert {"kind": "week", "week": 3, "dims": ["vert"], "factor": interview.LESS} in edits
    weeks = [{"start": (date(2026, 10, 5) + timedelta(weeks=i)).isoformat()} for i in range(8)]
    c, done, skipped = interview.apply_edits(edits, asdict(planner.Constraints()), weeks)
    assert c["long_day"] == 6 and 1 not in c["days"] and 6 in c["days"]
    assert c["adjust"] == [{"week": "2026-10-19", "dims": ["vert"], "factor": interview.LESS}]
    assert len(done) == 3 and not skipped
    _, done, skipped = interview.apply_edits([{"kind": "week", "week": 12, "dims": [], "factor": 0.85}], c, weeks)
    assert not done and skipped == ["the plan has weeks 1 to 8"]
    assert interview.from_edits([{"kind": "week", "week": 2, "dims": [], "direction": "more", "day": None, "minutes": None},
                                 {"kind": "long_day", "day": 9}]) == [
        {"kind": "week", "week": 2, "dims": ["miles", "vert"], "factor": interview.MORE}]


def test_speed_work_can_be_asked_for_in_plain_words():
    assert interview.rules_edits("add track workouts?") == [{"kind": "workout", "workout": "track"}]
    assert interview.rules_edits("I'd like a tempo run each week") == [{"kind": "workout", "workout": "tempo"}]
    assert interview.rules_edits("hill repeats please") == [{"kind": "workout", "workout": "hill_repeats"}]
    assert interview.rules_edits("no more speed work") == [{"kind": "workout", "workout": "none"}]
    c, done, _ = interview.apply_edits([{"kind": "workout", "workout": "track"}], asdict(planner.Constraints()), [])
    assert c["workout"] == "track" and "track intervals once a week" in done[0]
    assert interview.from_edits([{"kind": "workout", "workout": "tempo"}, {"kind": "workout", "workout": "sprint"}]) == [
        {"kind": "workout", "workout": "tempo"}]
    assert interview.cant_text("a race-pace run on Nov 3").startswith("I can't change that in the plan yet (a race-pace")


def test_an_adjusted_week_changes_only_that_week():
    acts = _acts()
    c = planner.Constraints()
    base = planner.build(_obj(), acts, _history(acts), c, TODAY)
    wk = base["weeks"][3]["start"]
    c.adjust = [{"week": wk, "dims": ["vert"], "factor": 0.85}]
    p = planner.build(_obj(), acts, _history(acts), c, TODAY)
    a, b = p["weeks"][3], base["weeks"][3]
    assert a["gain_ft"] < b["gain_ft"] * 0.9 and abs(a["miles"] - b["miles"]) < 0.5
    assert "Less vert this week, as you asked" in a["notes"]
    # The weeks after build from the level as planned, not from the lighter week.
    assert abs(p["weeks"][5]["gain_ft"] - base["weeks"][5]["gain_ft"]) < 1


def test_plan_by_conversation(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.get("/api/plan/talk").json()
    assert r["step"] == "objective" and r["mode"] == "setup" and not r["reads_with_ai"]
    day = date.today() + timedelta(weeks=10)
    r = client.post("/api/plan/talk", json={"text": f"Fall 30k on {day.isoformat()}, 18.6 mi"}).json()
    assert r["step"] == "objective" and "feet of climbing" in r["messages"][-1]["text"]
    r = client.post("/api/plan/talk", json={"text": "4,000 ft up"}).json()
    assert r["step"] == "goal" and r["messages"][-2]["text"].startswith("Training for Fall 30k on ")
    for step, answers in (("goal", {"goal": "Finish strong"}),
                          ("days", {"days": ["Tue", "Thu", "Sat", "Sun"], "long_day": "Sat"}),
                          ("time", {"weekday": "1 h", "weekend": "4 h"}),
                          ("terrain", {"terrain": "Rolling hills"})):
        assert r["step"] == step
        r = client.post("/api/plan/talk", json={"answers": answers}).json()
    r = client.post("/api/plan/talk", json={"answers": {"body": "Nothing to note", "feel": "Tired lately"}}).json()
    assert r["step"] == "draft" and "Here's your draft" in r["messages"][-1]["text"]
    assert "this week and next are a little lighter" in r["messages"][-1]["text"]
    assert [a["id"] for a in r["messages"][-1]["actions"]] == ["accept"]
    p = client.get("/api/plan").json()
    assert p["objective"]["name"] == "Fall 30k" and p["draft"] and p["constraints"]["days"] == [1, 3, 5, 6]
    assert p["constraints"]["weekday_min"] == 60 and len(p["constraints"]["adjust"]) == 2
    # A change by asking becomes a new draft.
    r = client.post("/api/plan/talk", json={"text": "move my long day to Sunday"}).json()
    assert "Done: long day on Sunday." in r["messages"][-1]["text"]
    assert client.get("/api/plan").json()["constraints"]["long_day"] == 6
    r = client.post("/api/plan/talk", json={"text": "make it more fun"}).json()
    assert r["messages"][-1]["text"].endswith(interview.EDIT_HELP)
    r = client.post("/api/plan/talk", json={"action": "accept"}).json()
    assert r["step"] == "done" and client.get("/api/plan").json()["accepted"]
    # Changing an accepted plan starts its own conversation; keeping it declines the draft.
    r = client.get("/api/plan/talk?mode=change").json()
    assert r["mode"] == "change" and r["step"] == "draft"
    r = client.post("/api/plan/talk", json={"text": "less vert in week 2"}).json()
    assert [a["id"] for a in r["messages"][-1]["actions"]] == ["accept", "keep"]
    client.post("/api/plan/talk", json={"action": "keep"})
    assert client.get("/api/plan").json()["draft"] is None


def test_typed_answers_go_to_the_cheaper_model(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    day = (date.today() + timedelta(weeks=9)).isoformat()
    fake = FakeClient({"name": "Fall 30k", "date": day, "distance_mi": None, "distance_km": 30, "gain_ft": None,
                       "gain_m": 1200, "loss_ft": None, "loss_m": None})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    r = client.post("/api/plan/talk", json={"text": "thirty k in the fall, twelve hundred meters of gain"}).json()
    assert r["step"] == "goal" and len(fake.calls) == 1 and fake.calls[0]["model"] == "claude-haiku-5-5"
    assert "Mode: plan intake." in fake.calls[0]["messages"][0]["content"]
    assert "18.6 mi, 3,937 ft up" in r["messages"][-2]["text"]
    # Typed words the rules can read whole don't call the model.
    client.post("/api/plan/talk/restart", json={"mode": "setup"})
    client.post("/api/plan/talk", json={"text": f"Fall 30k on {day}, 18.6 mi, 4,000 ft"})
    assert len(fake.calls) == 1
    # An existing objective can be kept with a tap.
    client.put("/api/plan/objective", json=OBJ | {"day": day})
    r = client.post("/api/plan/talk/restart", json={"mode": "setup"}).json()
    assert r["messages"][-1]["groups"][0]["key"] == "keep"
    r = client.post("/api/plan/talk", json={"answers": {"keep": "Keep it"}}).json()
    assert r["step"] == "goal"


def test_a_named_race_is_looked_up_and_confirmed(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    day = (date.today() + timedelta(weeks=30)).isoformat()
    found = ('Canyons Endurance Runs 100M is in late April.\n{"found": true, "name": "Canyons 100M", "date": "' + day
             + '", "distance_mi": 100.4, "gain_ft": 22000, "loss_ft": 22300, "source": "https://example.com/canyons"}')
    fake = FakeClient({"name": "Canyons 100M", "date": None, "distance_mi": 100, "distance_km": None, "gain_ft": None,
                       "gain_m": None, "loss_ft": None, "loss_m": None}, found)
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    client.put("/api/coach/note", json={"note": "Canyons 100M in April; I want more vert than miles."})
    r = client.post("/api/plan/talk", json={"text": "training for canyons 100m for next year"}).json()
    assert len(fake.calls) == 2
    # The athlete's training note goes to both the intake read and the lookup.
    assert all("more vert than miles" in c["messages"][0]["content"] for c in fake.calls)
    look = fake.calls[1]
    assert look["tools"][0]["type"] == "web_search_20260209" and look["model"] == "claude-opus-5-5"
    assert r["step"] == "objective" and "I looked it up (https://example.com/canyons)" in r["messages"][-1]["text"]
    assert "100.0 mi, 22,000 ft up and 22,300 ft down" in r["messages"][-1]["text"]
    assert r["messages"][-1]["groups"][0]["key"] == "confirm"
    r = client.post("/api/plan/talk", json={"answers": {"confirm": "Use these"}}).json()
    assert r["step"] == "goal" and "Training for Canyons 100M" in r["messages"][-2]["text"]


def test_a_paused_search_is_resumed_and_a_miss_asks_for_the_numbers():
    from talus import coach

    class Paused(FakeClient):
        def create(self, **kw):
            resp = super().create(**kw)
            if len(self.calls) == 1:
                resp.stop_reason = "pause_turn"
            else:
                resp.stop_reason = "end_turn"
            return resp

    fake = Paused("searching", '{"found": false, "name": null}')
    out, model, usage = coach.lookup_race(fake, "my local fun run", "2026-10-12", "claude-opus-5-5")
    assert out is None and len(fake.calls) == 2 and usage["output_tokens"] == 240
    assert fake.calls[1]["messages"][1]["role"] == "assistant"  # sent back to carry on, no extra user turn


def test_sentences_and_numbers_are_not_objective_names():
    assert "name" not in interview.rules_objective("i want to train for mountains", TODAY)
    o = interview.rules_objective("100miles 18000ft climbing 22000ft descend", TODAY)
    assert o["name"] == "100 mi objective" and o["gain_ft"] == 18000 and o["loss_ft"] == 22000
    assert interview.clean_name("Baldy 50k") == "Baldy 50k" and interview.clean_name("Canyons 100M") == "Canyons 100M"
    assert interview.general("just general fitness") and interview.general("i want to train for mountains")
    assert not interview.general("Baldy 50k on Jan 2")


def test_no_race_gets_a_fitness_block(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    r = client.post("/api/plan/talk", json={"text": "just general fitness"}).json()
    last = r["messages"][-1]
    assert r["step"] == "objective" and "12-week block" in last["text"] and last["groups"][0]["key"] == "confirm"
    r = client.post("/api/plan/talk", json={"answers": {"confirm": "Use this"}}).json()
    assert r["step"] == "goal" and "Training for Mountain fitness block" in r["messages"][-2]["text"]
