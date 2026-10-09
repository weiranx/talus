import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from talus import api, cli, coach
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
    assert kw["betas"] == [coach.FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["schema"] == coach.BRIEF_SCHEMA
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in kw


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
    return TestClient(api.app)


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
    row = api.get_store().coach_entry("brief", t["date"], coach.PROMPT_VERSION, coach.packet_hash(
        {**json.loads(api.get_store().db.execute("SELECT packet FROM coach_log").fetchone()[0]),
         "_model": coach.model()}))
    assert json.loads(row["packet"])["rule_call"]["call"] == t["call"]["call"]
    assert json.loads(row["usage"])["input_tokens"] == 900


def test_brief_reply_never_lets_the_coach_write_the_session():
    rules = {"call": "modify", "session": "Easy aerobic", "detail": "60 to 75 min in Zone 1.", "why": "r"}
    same = coach.brief_reply({"call": "modify", "why": "w"}, rules, 150)
    assert (same["session"], same["detail"], same["why"]) == ("Easy aerobic", "60 to 75 min in Zone 1.", "w")
    rest = coach.brief_reply({"call": "rest", "why": "w"}, rules, 150)
    assert rest["call"] == "rest" and "under 150 bpm" in rest["detail"] and rest["detail"] != rules["detail"]
    assert set(coach.BRIEF_SCHEMA["properties"]) == {"call", "why"}
