from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from talus import accounts, api
from talus.accounts import Accounts
from talus.config import data_dir
from talus.store import Store
from tests.conftest import sign_in
from tests.test_accounts import FRIEND, isolated  # noqa: F401  (autouse fixture)

ADMIN = {"email": "admin@example.com", "password": "admin long password"}


@pytest.fixture(autouse=True)
def admin_email(monkeypatch):
    monkeypatch.setattr(accounts, "ADMIN_EMAIL", ADMIN["email"])


def _store(account_id: int) -> Store:
    return Store(Accounts(data_dir()).user_dir(account_id))


def test_health_is_open_and_says_nothing_about_accounts(monkeypatch):
    monkeypatch.setenv("TALUS_REVISION", "abc1234def")
    sign_in(**ADMIN)
    r = TestClient(api.app).get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["revision"] == "abc1234def" and body["uptime_s"] >= 0
    assert set(body) == {"ok", "revision", "model_version", "started_at", "uptime_s"}
    assert TestClient(api.app).get("/api/status").status_code == 401


def test_status_shows_each_source_and_a_failed_sync():
    sign_in(**ADMIN)
    friend = sign_in(**FRIEND)
    friend.put("/api/settings/source", json={"api_key": "friend-secret-key", "athlete_id": "i99"})
    store = _store(2)
    old = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
    store.save_settings({"last_sync_intervals": {"at": old, "source": "intervals", "saved": 3, "error": None},
                         "last_import": {"at": old, "source": "file", "saved": 1, "error": "ValueError: bad file"}})
    body = friend.get("/api/status").json()
    rows = {r["key"]: r for r in body["sources"]}
    assert rows["intervals"]["state"] == "warn"  # connected but not synced for days
    assert rows["intervals"]["how"] == "API key" and rows["intervals"]["saved"] == 3
    assert rows["upload"]["state"] == "bad" and rows["upload"]["error"] == "ValueError: bad file"
    assert "garmin" not in rows and "watch" not in rows
    assert body["server"] is None
    assert "friend-secret-key" not in friend.get("/api/status").text

    store.save_settings({"last_sync_intervals": {"at": datetime.now().isoformat(timespec="seconds"),
                                                 "source": "intervals", "saved": 0, "error": None}})
    assert {r["key"]: r for r in friend.get("/api/status").json()["sources"]}["intervals"]["state"] == "ok"


def test_watch_sync_reads_its_time_only():
    friend = sign_in(**FRIEND)
    friend.put("/api/settings/sync-method", json={"method": "watch"})
    rows = {r["key"]: r for r in friend.get("/api/status").json()["sources"]}
    assert rows["watch"]["state"] == "idle" and "intervals" not in rows
    _store(1).save_settings({"last_sync_watch": datetime.now().isoformat(timespec="seconds")})
    rows = {r["key"]: r for r in friend.get("/api/status").json()["sources"]}
    assert rows["watch"]["state"] == "ok"


def test_coach_state(monkeypatch):
    owner = sign_in(**ADMIN)
    assert owner.get("/api/status").json()["coach"]["why"] == "no_key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    coach = owner.get("/api/status").json()["coach"]
    assert coach["state"] == "ok" and coach["last_call"] is None and coach["limit"] is None
    store = _store(1)
    store.db.execute("INSERT INTO coach_log (created_at, mode, subject, prompt_version, packet_hash, packet, model,"
                     " source, reply, attempts, fallback_reason, rejected) VALUES (?, 'brief', 'x', 'v', 'h', '{}',"
                     " 'm', 'rules', '{}', 1, 'api_error: APIConnectionError', '[]')",
                     (datetime.now().isoformat(timespec="seconds"),))
    store.db.commit()
    coach = owner.get("/api/status").json()["coach"]
    assert coach["state"] == "bad" and coach["why"] == "api_error" and coach["errors_24h"] == 1
    assert not coach["last_call"]["ok"]


def test_admin_gets_the_server_and_failing_syncs():
    admin = sign_in(**ADMIN)
    sign_in(**FRIEND)
    _store(2).save_settings({"last_sync_intervals": {"at": datetime.now().isoformat(timespec="seconds"),
                                                     "source": "intervals", "error": "HTTPError: 401"}})
    s = admin.get("/api/status").json()["server"]
    assert s["accounts"] == 2 and s["disk_state"] in {"ok", "warn", "bad"}
    assert s["failing_syncs"] == [{"email": FRIEND["email"], "source": "intervals.icu", "at": s["failing_syncs"][0]["at"]}]
    assert s["intervals_oauth"] == {"enabled": False, "connected": 0}


def test_read_ahead_waits_for_thresholds():
    owner = sign_in(**ADMIN)
    r = owner.get("/api/status").json()["read_ahead"]
    assert r == {"state": "idle", "ready": 0, "total": 0, "running": False, "waiting_for": "thresholds"}
