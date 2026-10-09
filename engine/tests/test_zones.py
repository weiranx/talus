import json

import numpy as np
import pytest

from talus import api, coach, zones
from talus.models import Thresholds
from tests.test_coach import _demo


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.setattr(api, "load_env", lambda: None)
    monkeypatch.setattr(api, "coach_client", lambda: None)


TH = Thresholds(resting=45, aet=150, ant=172, max=190)


def test_derived_floors():
    assert zones.derived_floors(TH, 3) == [150, 173]
    assert zones.derived_floors(TH, 5) == [135, 150, 161, 173]
    # A tiny aerobic gap still gives zones that each hold at least one bpm.
    assert zones.derived_floors(Thresholds(45, 170, 171, 190), 5) == [153, 170, 171, 172]
    assert [z["text"] for z in zones.describe([135, 150, 161, 173], 5)] == [
        "under 135 bpm", "135 to 149 bpm", "150 to 160 bpm", "161 to 172 bpm", "173 bpm and up"]


def test_three_zone_model_matches_the_engine_split():
    hr = np.array([140, 149, 150, 172, 173, 180], dtype=float)
    dt = np.full(hr.size, 60.0)
    th_split = {"z1": 2.0, "z2": 2.0, "z3": 2.0}
    assert zones.minutes(zones.hr_hist(hr, dt), zones.derived_floors(TH, 3)) == th_split
    assert zones.minutes(zones.hr_hist(hr, dt), zones.derived_floors(TH, 5)) == {
        "z1": 0.0, "z2": 2.0, "z3": 1.0, "z4": 1.0, "z5": 2.0}


def test_custom_floors_need_to_rise():
    assert zones.model(TH, {"zones": 5, "custom": {"5": [130, 145, 160, 170]}})["custom"]
    bad = zones.model(TH, {"zones": 5, "custom": {"5": [130, 120, 160, 170]}})
    assert not bad["custom"] and bad["floors"] == [135, 150, 161, 173]


def test_switching_zone_models_changes_time_in_zone_not_loads(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    before = client.get("/api/activities").json()
    assert set(before[0]["hr_zone_minutes"]) == {"z1", "z2", "z3"}

    r = client.put("/api/settings/zones", json={"zones": 5})
    assert r.status_code == 200 and r.json()["zones"]["zones"] == 5
    after = client.get("/api/activities").json()
    a0, b0 = after[0], before[0]
    assert set(a0["hr_zone_minutes"]) == {"z1", "z2", "z3", "z4", "z5"}
    assert sum(a0["hr_zone_minutes"].values()) == pytest.approx(sum(b0["hr_zone_minutes"].values()))
    assert [(a["cardio_load"], a["zone_minutes"]) for a in after] == [(b["cardio_load"], b["zone_minutes"]) for b in before]

    detail = client.get(f"/api/activities/{a0['id']}").json()
    assert detail["zone_model"]["zones"] == 5 and max(detail["streams"]["zone"]) <= 5
    assert set(detail["segments"][0]["hr_zone_minutes"]) == {"z1", "z2", "z3", "z4", "z5"}
    week = client.get("/api/load").json()["intensity_by_week"][-1]
    assert set(week["zones"]["share"]) == {"z1", "z2", "z3", "z4", "z5"} and "z1" in week["share"]
    t = client.get("/api/today").json()
    assert set(t["zones_28d"]["share"]) == {"z1", "z2", "z3", "z4", "z5"}

    packet = coach.brief_packet(t, api._activities(api.get_store()))
    assert packet["athlete"]["zones"].startswith("zone 1 under 135 bpm (recovery)")
    assert set(packet["intensity_28d_pct"]) == {"below_aet", "aet_to_ant", "above_ant"}
    p = coach.analysis_packet(detail, t["thresholds"], {}, __import__("datetime").date.today())
    assert set(p["activity"]["zone_minutes"]) == {"z1", "z2", "z3", "z4", "z5"}
    assert "above_ant_min" in p["segments"][0] and "Zone 5" in coach.rules_analysis(p)["job"]


def test_custom_floors_save_and_reset(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    r = client.put("/api/settings/zones", json={"zones": 5, "floors": [130, 148, 160, 170]}).json()
    assert r["zones"]["custom"] and r["zones"]["floors"] == [130, 148, 160, 170]
    th = client.get("/api/settings").json()["thresholds"]["zones"]
    assert th["list"][4]["text"] == "170 bpm and up" and th["options"]["3"]["derived"] == [150, 173]
    assert th["options"]["5"]["custom"] == [130, 148, 160, 170] and th["options"]["3"]["custom"] is None
    # Back to 3 zones keeps the 5-zone edits for later; reset clears them.
    assert client.put("/api/settings/zones", json={"zones": 3}).json()["zones"]["floors"] == [150, 173]
    assert client.put("/api/settings/zones", json={"zones": 5}).json()["zones"]["floors"] == [130, 148, 160, 170]
    assert client.put("/api/settings/zones", json={"zones": 5, "reset": True}).json()["zones"]["custom"] is False
    assert client.put("/api/settings/zones", json={"zones": 5, "floors": [150, 140, 160, 170]}).status_code == 422
    assert client.put("/api/settings/zones", json={"zones": 4}).status_code == 422


def test_old_summaries_are_rescored_when_zones_change(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    store = api.get_store()
    for r in store.activities():  # as scored before the histogram existed
        m = json.loads(r["summary"])
        m.pop("hr_hist")
        store.db.execute("UPDATE activity_score SET summary = ? WHERE activity_id = ?", (json.dumps(m), r["id"]))
    store.db.commit()
    assert client.get("/api/activities").json()[0]["hr_zone_minutes"] is not None  # 3-zone fallback
    assert client.put("/api/settings/zones", json={"zones": 5}).json()["rescored"] == 30
    assert all(a["hr_zone_minutes"] for a in client.get("/api/activities").json())
