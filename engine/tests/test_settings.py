import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from talus import api, cli, coach
from tests.test_api import _gpx
from tests.test_coach import FakeClient, _demo
from tests.test_intervals import source


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for k in ("INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID", "ANTHROPIC_API_KEY", "GARMIN_EMAIL", "TALUS_COACH_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "load_env", lambda: None)


def test_settings_before_anything_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    s = TestClient(api.app).get("/api/settings").json()
    assert s["thresholds"] is None and s["inferred"] is None
    assert s["source"] == {"name": "intervals.icu", "key_set": False, "athlete_id": None,
                           "garmin_fallback_set": False, "activities": 0, "latest_activity": None,
                           "last_sync": None}
    assert s["coach"]["enabled"] and not s["coach"]["key_set"]
    assert s["coach"]["brief_model"] == s["coach"]["analysis_model"] == coach.MODEL
    assert s["units"] == {"distance": "mi", "vert": "ft"}


def test_settings_never_return_key_values(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTERVALS_API_KEY", "secret-intervals")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic")
    monkeypatch.setenv("INTERVALS_ATHLETE_ID", "i746070")
    s = TestClient(api.app).get("/api/settings")
    assert "secret" not in s.text
    assert s.json()["source"]["key_set"] and s.json()["coach"]["key_set"]
    assert s.json()["source"]["athlete_id"] == "i746070"


def test_thresholds_save_rescore_and_follow_the_cli_rules(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    before = client.get("/api/activities").json()[0]
    r = client.put("/api/settings/thresholds", json={"resting": 50, "ant": 160, "max": 175})
    assert r.json() == {"ok": True, "rescored": 30}
    th = client.get("/api/settings").json()["thresholds"]
    assert (th["aet"], th["aet_provisional"]) == (141, True)  # 88% of 160
    assert [z["text"] for z in th["zones"]] == ["under 141 bpm", "141 to 160 bpm", "over 160 bpm"]
    after = client.get("/api/activities").json()[0]
    assert after["cardio_load"] != before["cardio_load"]  # same runs, new anchors
    client.put("/api/settings/thresholds", json={"resting": 50, "aet": 145, "ant": 160, "max": 175})
    th = client.get("/api/settings").json()["thresholds"]
    assert (th["aet"], th["aet_provisional"], th["source"]) == (145, False, "manual")
    bad = client.put("/api/settings/thresholds", json={"resting": 50, "aet": 165, "ant": 160, "max": 175})
    assert bad.status_code == 422 and "in order" in bad.json()["detail"]


def test_coach_off_shows_rules_without_calling(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    body = {"enabled": False, "brief_model": "claude-opus-5-5", "analysis_model": "claude-opus-5-5"}
    assert client.put("/api/settings/coach", json=body).json() == {"ok": True}
    b = client.get("/api/coach/brief").json()
    assert (b["source"], b["fallback_reason"], b["log_id"]) == ("rules", "off", None)
    assert fake.calls == []


def test_coach_uses_the_chosen_model_per_mode(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    t = client.get("/api/today").json()
    fake = FakeClient({"call": "rest", "why": f"Your {t['readiness']['legs']['limiter']} load is high."},
                      {"call": "rest", "why": f"Your {t['readiness']['legs']['limiter']} load is high."})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    client.get("/api/coach/brief")
    client.put("/api/settings/coach", json={"enabled": True, "brief_model": "claude-haiku-5-5",
                                            "analysis_model": "claude-opus-5-5"})
    client.get("/api/coach/brief")  # a new model is a new reply, not the cached one
    assert [c["model"] for c in fake.calls] == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert "fallbacks" in fake.calls[0] and "fallbacks" not in fake.calls[1]  # Haiku has no fallback
    est = client.get("/api/settings").json()["coach"]["estimate"]
    haiku, opus = est["brief"]["claude-haiku-5-5"], est["brief"]["claude-opus-5-5"]
    assert haiku["from_log"] and haiku["per_call"] < opus["per_call"] / 30 and haiku["calls_per_month"] == 30
    assert est["analysis"]["claude-opus-5-5"]["calls_per_month"] == 30 and not est["analysis"]["claude-opus-5-5"]["from_log"]
    assert client.put("/api/settings/coach", json={"enabled": True, "brief_model": "gpt",
                                                   "analysis_model": "claude-opus-5-5"}).status_code == 422


def test_sync_now_records_the_last_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(api.app)
    assert client.post("/api/sync", json={"days": 30}).status_code == 409
    monkeypatch.setenv("INTERVALS_API_KEY", "k")
    monkeypatch.setattr("talus.sources.intervals.IntervalsSource", lambda: source())
    days = (date.today() - date(2026, 9, 1)).days  # the mock serves activities from 2026-09-01
    r = client.post("/api/sync", json={"days": days})
    assert r.status_code == 200
    out = r.json()
    assert (out["listed"], out["new"], out["saved"], out["error"]) == (2, 2, 2, None)
    last = client.get("/api/settings").json()["source"]["last_sync"]
    assert last["new"] == 2 and last["since"] == out["since"]
    assert json.dumps(last).count("secret") == 0


def test_settings_show_thresholds_inferred_from_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in range(1, 4):  # three 40-min runs at a steady 150 bpm
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k}.gpx"
        f.write_bytes(_gpx(d, n=2400, hr=150))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", *files])
    inf = TestClient(api.app).get("/api/settings").json()["inferred"]
    assert (inf["runs_with_hr"], inf["days"]) == (3, 180)
    assert inf["ant"]["value"] == round(150 * 0.97) and inf["ant"]["confidence"] == "medium"
    assert inf["ant"]["activities"][0].keys() == {"id", "day", "name"}
    assert inf["max"]["value"] == 150 and inf["aet"]["method"]
