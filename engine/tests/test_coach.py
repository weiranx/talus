import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from talus import api, cli, coach
from tests.conftest import sign_in
from tests.test_api import _gpx

PACKET = {
    "rule_call": {"call": "modify", "session": "Easy aerobic, low descent",
                  "detail": "60 to 75 min in Zone 1, under 150 bpm. Keep descent under 500 ft.", "why": "x"},
    "ledgers": {"descent": {"vs_normal_pct": 42, "last_7_days_per_day": 512, "normal_per_day": 361}},
    "activity": {"gain_ft": 4512, "times_usual_day": 1.47, "hike_share_pct": 38, "moving_time": "4:27"},
}


def test_numbers_in_text():
    assert coach.numbers_in("4,500 ft on Oct 7, then 1.5x and 60-75 min") == ["4,500", "7", "1.5", "60", "75"]


def test_traceable_numbers():
    allowed = coach.allowed_numbers(PACKET)
    for ok in ("4,512", "4,500", "4,510", "1.5", "42", "512", "500", "60", "75", "150", "38", "27", "4"):
        assert coach.traceable(ok, allowed), ok
    for bad in ("4,600", "1.6", "43", "90", "13.1", "11", "3", "10"):
        assert not coach.traceable(bad, allowed), bad
    # No shortcuts: a 1.5 mi segment isn't 150 bpm, 12 isn't "10", and small counts aren't free.
    assert not coach.traceable("150", {1.5})
    assert not coach.traceable("10", {12.0}) and not coach.traceable("10", set())
    assert coach.traceable("3,000", {2946.0})


def test_check_flags_invented_numbers_and_a_bolder_call():
    reply = {"call": "go", "why": "Run 18 mi with 3,000 ft up, under 150 bpm. Descent is 42% above normal."}
    problems = coach.check(reply, PACKET, "brief")
    assert "18" in problems[0] and "3,000" in problems[0] and "150" not in problems[0]
    assert "less cautious" in problems[1]
    calm = {"call": "rest", "why": "Descent is 42% above normal."}
    assert coach.check(calm, PACKET, "brief") == []


class FakeClient:
    """Stands in for anthropic.Anthropic: returns recorded replies in order."""

    def __init__(self, *replies, stop_reason="end_turn"):
        self.replies = list(replies)
        self.calls = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        body = self.replies.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=body if isinstance(body, str) else json.dumps(body))],
            stop_reason=self.stop_reason, model="claude-test",
            usage=SimpleNamespace(input_tokens=900, output_tokens=120, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


FALLBACK = {"call": "modify", "session": "rules", "detail": "rules", "why": "rules"}
GOOD = {"call": "modify",
        "why": "Descent is 42% above your normal, 512 a day against 361."}
BAD = dict(GOOD, why="Run 9.3 mi under 150 bpm.")


def test_run_passes_a_clean_reply_and_sends_the_right_request():
    client = FakeClient(GOOD)
    r = coach.run("brief", PACKET, FALLBACK, client)
    assert (r.source, r.reply, r.attempts) == ("coach", GOOD, 1)
    kw = client.calls[0]
    # The brief runs on Haiku, which has no server-side fallback, so it goes without one.
    assert kw["model"] == "claude-haiku-5-5" and "betas" not in kw and "fallbacks" not in kw
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["schema"] == coach.BRIEF_SCHEMA
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in kw
    assert "Style:" not in kw["messages"][0]["content"]


def test_analysis_runs_on_opus_with_fallback_and_style_changes_only_the_voice():
    client = FakeClient({k: "Fine." for k in coach.ANALYSIS_FIELDS})
    r = coach.run("analysis", PACKET, {}, client, style="blunt")
    kw = client.calls[0]
    assert r.source == "coach" and kw["model"] == "claude-opus-5-5"
    assert kw["betas"] == [coach.FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["output_config"]["effort"] == "medium"
    assert "Style: blunt" in kw["messages"][0]["content"]
    assert kw["system"][0]["text"] == coach.SYSTEM  # one cached system prompt for every style
    for style in coach.STYLES.values():
        assert not coach.numbers_in(style["prompt"])  # nothing in the style the coach could quote


def test_cache_key_changes_with_model_and_style():
    keys = {coach.cache_key(PACKET, m, s) for m in ("claude-haiku-5-5", "claude-opus-5-5") for s in coach.STYLES}
    assert len(keys) == 2 * len(coach.STYLES)
    # Plain keeps the key replies were cached under before styles existed.
    assert coach.cache_key(PACKET, "m") == coach.packet_hash({**PACKET, "_model": "m"})


def test_run_retries_once_with_the_rejected_numbers():
    client = FakeClient(BAD, GOOD)
    r = coach.run("brief", PACKET, FALLBACK, client)
    assert (r.source, r.attempts) == ("coach", 2)
    assert "9.3" in client.calls[1]["messages"][0]["content"]
    assert r.rejected and "9.3" in r.rejected[0]


def test_run_falls_back_to_rules():
    r = coach.run("brief", PACKET, FALLBACK, FakeClient(BAD, BAD))
    assert (r.source, r.reply, r.fallback_reason) == ("rules", FALLBACK, "rejected")
    r = coach.run("brief", PACKET, FALLBACK, FakeClient(GOOD, stop_reason="refusal"))
    assert (r.source, r.fallback_reason) == ("rules", "refusal")
    r = coach.run("brief", PACKET, FALLBACK, FakeClient("not json"))
    assert r.source == "rules" and r.fallback_reason.startswith("api_error")
    assert coach.run("brief", PACKET, FALLBACK, None).fallback_reason == "no_key"


def _demo(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in range(30, 0, -1):
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:02d}.gpx"
        f.write_bytes(_gpx(d, climb_per_s=1.0 if k == 1 else 0.3))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", *files])
    return sign_in()


def _keys(v) -> set:
    if isinstance(v, dict):
        return set(v) | set().union(*(_keys(x) for x in v.values()))
    if isinstance(v, list):
        return set().union(*(_keys(x) for x in v))
    return set()


def test_packets_hold_no_gps_or_names(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "coach_client", lambda: None)
    t = client.get("/api/today").json()
    packet = coach.brief_packet(t, api._activities(api.get_store()))
    assert not _keys(packet) & {"lat", "lon", "name", "streams"}
    assert packet["rule_call"]["call"] == t["call"]["call"]
    aid = client.get("/api/activities").json()[0]["id"]
    a = client.get(f"/api/activities/{aid}").json()
    p = coach.analysis_packet(a, t["thresholds"], {}, date.today())
    assert not _keys(p) & {"lat", "lon", "name", "streams"}
    assert p["segments"][0]["label"].endswith(" 1")


def test_coach_endpoints_without_a_key_show_rules(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "coach_client", lambda: None)
    b = client.get("/api/coach/brief").json()
    t = client.get("/api/today").json()
    assert b["source"] == "rules" and b["fallback_reason"] == "no_key" and b["log_id"] is None
    assert b["reply"]["call"] == t["call"]["call"]
    aid = client.get("/api/activities").json()[0]["id"]
    r = client.get(f"/api/coach/activity/{aid}").json()
    assert r["source"] == "rules" and set(r["reply"]) == set(coach.ANALYSIS_FIELDS)
    assert " mi with " in r["reply"]["job"] and " ft up" in r["reply"]["job"]


def test_coach_reply_is_logged_reused_and_rated(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    t = client.get("/api/today").json()
    reply = {"call": "rest", "why": f"Your {t['readiness']['legs']['limiter']} load is high."}
    fake = FakeClient(reply)
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    first = client.get("/api/coach/brief").json()
    again = client.get("/api/coach/brief").json()
    assert first["source"] == "coach" and first["log_id"]
    # The coach makes the call and explains it; the session is the engine's for that call.
    assert first["reply"]["call"] == "rest" and first["reply"]["why"] == reply["why"]
    assert first["reply"]["session"] == (t["call"]["session"] if t["call"]["call"] == "rest"
                                         else "Rest or 30 min very easy")
    assert again["log_id"] == first["log_id"] and len(fake.calls) == 1
    assert client.post(f"/api/coach/feedback/{first['log_id']}", json={"rating": 1}).json() == {"ok": True}
    assert client.get("/api/coach/brief").json()["feedback"] == 1
    assert client.post("/api/coach/feedback/9999", json={"rating": 1}).status_code == 404
    row = api.get_store().coach_entry("brief", t["date"], coach.PROMPT_VERSION, coach.cache_key(
        json.loads(api.get_store().db.execute("SELECT packet FROM coach_log").fetchone()[0]), coach.BRIEF_MODEL))
    assert json.loads(row["packet"])["rule_call"]["call"] == t["call"]["call"]
    assert json.loads(row["usage"])["input_tokens"] == 900


def test_brief_reply_never_lets_the_coach_write_the_session():
    rules = {"call": "modify", "session": "Easy aerobic", "detail": "60 to 75 min in Zone 1.", "why": "r"}
    same = coach.brief_reply({"call": "modify", "why": "w"}, rules, 150)
    assert (same["session"], same["detail"], same["why"]) == ("Easy aerobic", "60 to 75 min in Zone 1.", "w")
    rest = coach.brief_reply({"call": "rest", "why": "w"}, rules, 150)
    assert rest["call"] == "rest" and "under 150 bpm" in rest["detail"] and rest["detail"] != rules["detail"]
    assert set(coach.BRIEF_SCHEMA["properties"]) == {"call", "why"}


def test_style_is_saved_in_settings_and_drives_the_coach(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    fake = FakeClient({"call": "rest", "why": "Rest."}, {"call": "rest", "why": "Rest, really."})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    body = {"enabled": True, "brief_model": "claude-haiku-5-5", "analysis_model": "claude-opus-5-5"}
    assert client.put("/api/settings/coach", json=body | {"style": "loud"}).status_code == 422
    client.put("/api/settings/coach", json=body | {"style": "teaching"})
    assert client.get("/api/settings").json()["coach"]["style"] == "teaching"
    client.put("/api/settings/coach", json=body)  # no style keeps the saved one
    assert client.get("/api/settings").json()["coach"]["style"] == "teaching"
    client.get("/api/coach/brief")
    assert "Style: teaching" in fake.calls[0]["messages"][0]["content"]
    client.put("/api/settings/coach", json=body | {"style": "plain"})
    client.get("/api/coach/brief")  # a new style is a new reply, not the cached one
    assert len(fake.calls) == 2 and "Style:" not in fake.calls[1]["messages"][0]["content"]


def test_import_writes_the_coach_right_away(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    analysis = {k: "Fine." for k in coach.ANALYSIS_FIELDS}
    fake = FakeClient({"call": "rest", "why": "Rest."}, analysis, analysis)
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    files = []
    for k in (40, 2, 1):  # the 40-day-old run is history: no read is written for it
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:02d}.gpx"
        f.write_bytes(_gpx(d))
        files.append(str(f))
    cli.main(["import", *files])
    out = capsys.readouterr().out
    assert "Coach brief: rest (coach)" in out and out.count("Coach read for activity") == 2
    assert [c["model"] for c in fake.calls] == ["claude-haiku-5-5", "claude-opus-5-5", "claude-opus-5-5"]
    # Opening the pages reuses what the import wrote.
    client = sign_in()
    assert client.get("/api/coach/brief").json()["source"] == "coach"
    for a in client.get("/api/activities").json()[:2]:
        assert client.get(f"/api/coach/activity/{a['id']}").json()["source"] == "coach"
    assert len(fake.calls) == 3
    cli.main(["import", "--no-coach", files[0]])
    assert "Coach" not in capsys.readouterr().out


def _act(i: int, day: str, miles: float, gain: float, sport: str = "run", hours: float = 1.0) -> dict:
    return {"id": i, "day": day, "start_time": f"{day}T14:00:00", "sport": sport, "distance_mi": miles,
            "gain_ft": gain, "loss_ft": gain, "effort_mi": miles + gain / 528, "moving_time_s": hours * 3600,
            "cardio_load": 60 * hours, "climb_load": gain / 10, "descent_load": gain / 12,
            "zone_minutes": {"z1": 48 * hours, "z2": 9 * hours, "z3": 3 * hours}}


def test_weekly_history_and_rest_pattern_give_the_coach_trends_without_math():
    day = date(2026, 10, 14)  # a Wednesday
    acts = [_act(1, "2026-10-04", 20.0, 5000, hours=5), _act(2, "2026-10-06", 6.0, 800),
            _act(3, "2026-10-12", 8.0, 1200), _act(4, "2026-10-13", 5.0, 400)]
    weeks = coach.weekly_history(acts, day, 3)
    assert [w["week_start"] for w in weeks] == ["2026-09-28", "2026-10-05", "2026-10-12"]
    assert weeks[0] | {"longest": None} == {
        "week_start": "2026-09-28", "complete": True, "activities": 1, "days_active": 1, "distance_mi": 20.0,
        "gain_ft": 5000, "loss_ft": 5000, "hours": 5.0, "below_aet_pct": 80, "longest": None}
    assert weeks[2]["complete"] is False and weeks[2]["distance_mi"] == 13.0 and weeks[2]["gain_ft"] == 1600
    assert weeks[2]["longest"]["distance_mi"] == 8.0
    assert coach.rest_pattern(acts, day) == {"training_days_in_a_row_before_today": 2, "rest_days_last_14": 10,
                                             "trained_today": False}
    b = coach.bests(acts, day)
    assert b["longest"]["day"] == "2026-10-04" and b["most_gain"]["gain_ft"] == 5000


def test_analysis_compares_with_similar_earlier_activities():
    this = _act(9, "2026-10-10", 10.0, 2640)  # 15 effort mi
    acts = [_act(1, "2026-09-01", 9.0, 2700), _act(2, "2026-09-20", 10.0, 2000),
            _act(3, "2026-09-25", 3.0, 100), _act(4, "2026-09-26", 10.0, 2640, sport="hike"),
            _act(5, "2026-10-11", 10.0, 2640), this]
    sim = coach.similar_recent(this, acts)
    # Same sport, before it, within half to one and a half times its effort; oldest first.
    assert [s["day"] for s in sim] == ["2026-09-01", "2026-09-20"]
    assert sim[0]["min_per_effort_mi"] == round(60 / (9 + 2700 / 528), 1)
    assert coach.biggest_in(this, acts) == ["distance", "moving time"]


def test_chat_packet_carries_history_and_keeps_miles_to_a_tenth(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    t = client.get("/api/today").json()
    acts = api._activities(api.get_store())
    p = coach.chat_packet("How was my month?", [], t, None, None, [], acts, date.today(), acts)
    assert len(p["weeks"]) == coach.HISTORY_WEEKS and p["bests_90d"]["longest"]["distance_mi"] > 0
    assert p["ledgers"]["distance"]["normal_per_day"] == round(t["ledgers"]["distance"]["fitness"], 1)
    assert p["week_so_far"] is not None and p["overnight"] is not None and p["rest"]["rest_days_last_14"] >= 0
    # Every weekly total is a number the coach may quote.
    allowed = coach.allowed_numbers(p)
    assert all(coach.traceable(f"{w['distance_mi']}", allowed) for w in p["weeks"])
    brief = coach.brief_packet(t, acts, acts)
    assert len(brief["weeks"]) == coach.BRIEF_WEEKS and "rest" in brief


def test_chat_prompt_answers_workout_asks_instead_of_listing_limits():
    assert "own run on the Plan page calendar" in coach.SYSTEM and "intervals" in coach.SYSTEM
    assert "Never reply with only a list" in coach.SYSTEM


def test_training_note_is_drafted_saved_and_read_by_every_coach_answer(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "coach_client", lambda: None)
    assert client.get("/api/coach/note").json() == {"note": ""}
    # Without a key the draft is built from the answers alone.
    r = client.post("/api/coach/note/draft", json={"training_for": "a 50k in the spring", "wants": "more vert",
                                                   "avoid": ""}).json()
    assert r["source"] == "rules" and r["note"] == "I'm training for a 50k in the spring. I want more vert."
    assert client.post("/api/coach/note/draft", json={}).status_code == 422
    # With the coach on, the draft reads the answers and the athlete's recent weeks.
    fake = FakeClient({"note": "I want more vert for a 50k."})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    r = client.post("/api/coach/note/draft", json={"training_for": "a 50k", "wants": "more vert"}).json()
    assert r == r | {"source": "coach", "note": "I want more vert for a 50k."}
    sent = fake.calls[0]["messages"][0]["content"]
    assert "Mode: training note." in sent and '"weeks"' in sent and "more vert" in sent
    # Saved, it goes into the brief and chat packets.
    note = "I want more vert and one long day a week."
    assert client.put("/api/coach/note", json={"note": f"  {note} "}).json() == {"ok": True}
    assert client.get("/api/coach/note").json() == {"note": note}
    fake = FakeClient({"call": "rest", "why": "Rest."}, {"answer": "Yes.", "used": []})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    client.get("/api/coach/brief")
    client.post("/api/coach/chat", json={"question": "More vert?"})
    assert all(f'"athlete_note": "{note}"' in c["messages"][0]["content"] for c in fake.calls)
    assert client.put("/api/coach/note", json={"note": "x" * (coach.NOTE_MAX + 1)}).status_code == 422


def test_load_read_runs_on_the_cheap_model_and_reads_weeks_and_room(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    monkeypatch.setattr(api, "coach_client", lambda: None)
    r = client.get("/api/coach/load").json()
    assert r["source"] == "rules" and set(r["reply"]) == set(coach.LOAD_FIELDS)
    assert " mi with " in r["reply"]["last_week"]
    reply = {k: "Steady." for k in coach.LOAD_FIELDS}
    fake = FakeClient(reply)
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    client.put("/api/coach/note", json={"note": "More vert."})
    first = client.get("/api/coach/load").json()
    again = client.get("/api/coach/load").json()
    assert first["source"] == "coach" and again["log_id"] == first["log_id"] and len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == coach.BRIEF_MODEL and "Mode: load read." in call["messages"][0]["content"]
    packet = json.loads(call["messages"][0]["content"].split("Data:\n", 1)[1])
    assert len(packet["weeks"]) == 8 and "room" in packet["next_7_days"] and packet["athlete_note"] == "More vert."
