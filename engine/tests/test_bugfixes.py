"""Regressions for bugs found reviewing main after the parallel PRs landed."""

from __future__ import annotations

import base64
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from talus import api, cli, coach
from talus import settings as app_settings
from talus.accounts import AccountError, Accounts
from talus.config import data_dir
from talus.sources import intervals
from talus.sources.base import RawFile, RemoteActivity
from talus.store import Store
from tests.conftest import sign_in
from tests.test_api import _gpx
from tests.test_coach import BAD, FALLBACK, GOOD, PACKET, FakeClient

FRIEND = {"email": "friend@example.com", "password": "another long password"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in ("INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID", "ANTHROPIC_API_KEY", "TALUS_SIGNUP",
              "TALUS_COACH_CAP_USD", "TALUS_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "load_env", lambda: None)
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))


def test_a_blank_key_never_syncs_with_the_owners_key(monkeypatch):
    monkeypatch.setenv("INTERVALS_API_KEY", "OWNER-SECRET")
    sign_in()
    friend = sign_in(**FRIEND)
    assert friend.put("/api/settings/source", json={"api_key": "        ", "athlete_id": "0"}).status_code == 422
    with pytest.raises(RuntimeError):
        intervals.IntervalsSource.from_credential({"kind": "api_key", "api_key": "", "athlete_id": "0"})
    src = intervals.IntervalsSource.from_credential({"kind": "api_key", "api_key": " friend-key ", "athlete_id": "0"})
    auth = src.client.auth._auth_header
    assert base64.b64decode(auth.split()[1]).decode() == "API_KEY:friend-key"


def test_a_damaged_file_is_skipped_and_threshold_saves_still_work(tmp_path, monkeypatch):
    client = sign_in()
    client.put("/api/settings/thresholds", json={"resting": 50, "ant": 170, "max": 190})
    client.put("/api/settings/source", json={"api_key": "owner-key-123", "athlete_id": "0"})
    start = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    acts = [RemoteActivity("intervals", "bad", start, name="bad"),
            RemoteActivity("intervals", "good", start + timedelta(hours=3), name="good")]
    monkeypatch.setattr(intervals.IntervalsSource, "list_activities", lambda self, s, u=None: acts)
    monkeypatch.setattr(intervals.IntervalsSource, "list_wellness", lambda self, s, u=None: [])
    monkeypatch.setattr(intervals.IntervalsSource, "fetch",
                        lambda self, a: RawFile(b"<gpx><trk><trkpt", "gpx") if a.source_id == "bad"
                        else RawFile(_gpx(a.start_time), "gpx"))
    r = client.post("/api/sync", json={"days": 5}).json()
    assert r["error"] is None and r["saved"] == 2
    rows = {row["source_id"]: row for row in Store(tmp_path / "data" / "users" / "1").activities()}
    assert rows["good"]["summary"] is not None and rows["bad"]["summary"] is None

    lenient = TestClient(api.app, raise_server_exceptions=False, cookies=client.cookies)
    assert lenient.put("/api/settings/thresholds", json={"resting": 50, "ant": 172, "max": 190}).status_code == 200
    bad = lenient.get(f"/api/activities/{rows['bad']['id']}")
    assert bad.status_code == 422 and "Could not read" in bad.json()["detail"]
    assert lenient.get(f"/api/activities/{rows['good']['id']}").status_code == 200


def test_a_retry_counts_both_calls_against_the_cap():
    r = coach.run("brief", PACKET, FALLBACK, FakeClient(BAD, GOOD))
    assert r.usage["input_tokens"] == 1800 and r.usage["output_tokens"] == 240


def test_unrecognised_model_names_still_cost_money():
    assert app_settings._priced_as("claude-opus-5-5-20260801") == "claude-opus-5-5"
    assert app_settings._priced_as("claude-haiku-5-5") == "claude-haiku-5-5"
    assert app_settings._priced_as("claude-test") == "claude-opus-5-5"  # the dearest


def test_serve_startup_leaves_no_legacy_database(tmp_path, monkeypatch):
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    cli.main(["serve"])
    assert not (tmp_path / "data" / "talus.db").exists()
    s = TestClient(api.app).get("/api/auth/status").json()
    assert s["existing_data"] is False


def test_an_unreadable_key_reads_as_not_set(tmp_path, monkeypatch):
    client = sign_in()
    client.put("/api/settings/source", json={"api_key": "owner-key-123", "athlete_id": "0"})
    (tmp_path / "data" / "secret.key").unlink()
    accounts = Accounts(data_dir())
    assert accounts.credential(1, "intervals") is None


def test_the_same_email_twice_is_an_account_error(tmp_path):
    accounts = Accounts(data_dir())
    accounts.create("a@example.com", "a long enough password")
    monkey = accounts.by_email
    accounts.by_email = lambda email: None  # as if the other sign-up had not committed yet
    with pytest.raises(AccountError):
        accounts.create("a@example.com", "a long enough password")
    accounts.by_email = monkey


def test_a_gps_dropout_does_not_inflate_speed():
    import numpy as np

    from talus.models import Thresholds
    from talus.score import score
    from tests.conftest import make_streams
    athlete = Thresholds(45, 150, 172, 190, threshold_pace_s_per_mi=480)
    s = make_streams([(3.0, 0, 3.0)], hr=None)
    clean = score(s, athlete)
    # Drop 120 samples in the middle: one 121 s gap covering 363 m at the same 3 m/s.
    keep = np.r_[0:800, 920:s.time.size]
    gap = make_streams([(3.0, 0, 3.0)], hr=None)
    gap.time, gap.distance, gap.altitude = gap.time[keep], gap.distance[keep], gap.altitude[keep]
    gap.cadence = gap.cadence[keep]
    dropped = score(gap, athlete)
    assert dropped.cardio_estimated and dropped.cardio_load <= clean.cardio_load * 1.02


def test_trail_runs_count_for_threshold_inference():
    from talus import infer
    assert "trailrun" in infer.FOOT_SPORTS and "virtualrun" in infer.FOOT_SPORTS


def test_the_validator_allows_numbers_from_keys_and_the_prompt():
    packet = {"activity": {"minutes_above_8000ft": 12, "gain_by_grade_ft": {"<8": 400, "8-20": 2100}},
              "ledgers": {"climb": {"vs_normal_pct": 35}}, "rule_call": {"call": "modify", "why": "x"}}
    ok = {"call": "modify", "why": "Climb is 35% above your 42-day normal; 12 min above 8,000 ft on 8 to 20% grades."}
    assert coach.check(ok, packet, "brief") == []
    assert coach.check({"call": "modify", "why": "Climb is 3% above normal."}, packet, "brief")
