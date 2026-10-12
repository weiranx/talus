"""The first-run flow: steps, resuming, skipping, and the background history import."""

import time
from datetime import date

import pytest

from talus import api
from tests.conftest import sign_in
from tests.test_coach import _demo
from tests.test_intervals import source


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for k in ("INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID", "ANTHROPIC_API_KEY", "GARMIN_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "load_env", lambda: None)


def _wait_for_import():
    for _ in range(300):
        with api._imports_guard:
            if not api._imports:
                break
        time.sleep(0.05)
    for t in list(api._inferring.values()):
        t.join(timeout=60)


def test_a_new_account_starts_at_connect(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    ob = sign_in().get("/api/onboarding").json()
    assert ob["needed"] and not ob["established"] and ob["next"] == "connect"
    assert [s["key"] for s in ob["steps"]] == ["connect", "import", "thresholds", "sleep", "coach", "tour"]
    assert [s["required"] for s in ob["steps"]] == [True, True, False, False, False, False]
    assert ob["import_choices"] == {"6w": 42, "6m": 180, "all": 730}


def test_an_account_set_up_before_the_flow_never_sees_it(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    ob = client.get("/api/onboarding").json()
    assert ob["established"] and not ob["needed"]
    # Opening the guide from Settings and stepping through it doesn't make it open on its own,
    # and skipping the coach step there leaves the coach as it was.
    ob = client.put("/api/onboarding", json={"action": "skip", "step": "coach"}).json()
    assert not ob["needed"] and client.get("/api/settings").json()["coach"]["enabled"]


def test_leaving_setup_before_coach_consent_leaves_the_coach_off(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    client = sign_in()
    client.put("/api/onboarding", json={"action": "finish"})
    assert not client.get("/api/settings").json()["coach"]["enabled"]


def test_required_steps_cannot_be_skipped_and_skips_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    client = sign_in()
    r = client.put("/api/onboarding", json={"action": "skip", "step": "connect"})
    assert r.status_code == 422
    ob = client.put("/api/onboarding", json={"action": "skip", "step": "sleep"}).json()
    assert ob["started"] and next(s for s in ob["steps"] if s["key"] == "sleep")["skipped"]
    assert ob["next"] == "connect"  # skipped steps are passed over, required ones are not
    # Saving sleep need afterwards counts as done, not skipped.
    client.put("/api/settings/readiness", json={"sleep_need_h": 7.5})
    sleep = next(s for s in client.get("/api/onboarding").json()["steps"] if s["key"] == "sleep")
    assert sleep["done"] and not sleep["skipped"]


def test_coach_consent_sets_the_coach(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    client = sign_in()
    client.put("/api/onboarding", json={"action": "skip", "step": "coach"})
    assert not client.get("/api/settings").json()["coach"]["enabled"]  # no consent, no coach
    bad = client.put("/api/onboarding", json={"action": "done", "step": "coach", "coach_enabled": True,
                                              "coach_style": "nope"})
    assert bad.status_code == 422
    ob = client.put("/api/onboarding", json={"action": "done", "step": "coach", "coach_enabled": True,
                                             "coach_style": "plain"}).json()
    assert ob["coach"]["enabled"] and next(s for s in ob["steps"] if s["key"] == "coach")["done"]


def test_import_runs_in_the_background_then_finish_ends_the_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    client = sign_in()
    assert client.post("/api/onboarding/import", json={"range": "all"}).status_code == 409
    client.put("/api/settings/source", json={"api_key": "secret-key-1234", "athlete_id": "i42"})
    monkeypatch.setattr("talus.sources.intervals.IntervalsSource.from_credential", lambda cred: source())
    days = (date.today() - date(2026, 9, 1)).days  # the mock serves activities from 2026-09-01
    monkeypatch.setitem(api.IMPORT_DAYS, "all", days)
    r = client.post("/api/onboarding/import", json={"range": "all"})
    assert r.status_code == 200 and r.json()["state"] == "running" and r.json()["days"] == days
    _wait_for_import()
    ob = client.get("/api/onboarding").json()
    assert ob["import"]["state"] == "done" and ob["import"]["saved"] == 2 and ob["import"]["error"] is None
    assert ob["import"]["total"] == 2 and ob["import"]["done"] == 2
    assert ob["activities"] == 2 and ob["next"] == "thresholds"
    assert "secret" not in str(ob)
    ob = client.put("/api/onboarding", json={"action": "finish"}).json()
    assert ob["finished"] and not ob["needed"]
    assert client.put("/api/onboarding", json={"action": "restart"}).json()["needed"]


def test_a_failed_import_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    client = sign_in()
    client.put("/api/settings/source", json={"api_key": "secret-key-1234"})

    def broken(cred):
        raise RuntimeError("intervals.icu said 401")
    monkeypatch.setattr("talus.sources.intervals.IntervalsSource.from_credential", broken)
    client.post("/api/onboarding/import", json={"range": "6w"})
    _wait_for_import()
    ob = client.get("/api/onboarding").json()
    assert "401" in ob["import"]["error"] and ob["next"] == "import"
