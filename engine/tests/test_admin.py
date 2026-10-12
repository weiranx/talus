import sqlite3

import pytest
from fastapi.testclient import TestClient

from talus import accounts, api
from talus.accounts import Accounts
from tests.conftest import sign_in
from tests.test_accounts import FRIEND, isolated  # noqa: F401  (autouse fixture)

ADMIN = {"email": "admin@example.com", "password": "admin long password"}
OTHER = {"email": "other@example.com", "password": "other long password"}


@pytest.fixture(autouse=True)
def admin_email(monkeypatch):
    monkeypatch.setattr(accounts, "ADMIN_EMAIL", ADMIN["email"])


def test_only_the_admin_email_sees_the_admin_page():
    admin = sign_in(**ADMIN)  # first account: also the owner
    friend = sign_in(**FRIEND)
    assert admin.get("/api/auth/status").json()["account"]["is_admin"]
    assert not friend.get("/api/auth/status").json()["account"]["is_admin"]
    assert admin.get("/api/admin").status_code == 200
    for method, path, body in (("get", "/api/admin", None), ("put", "/api/admin/signup", {"open": False}),
                               ("post", "/api/admin/accounts/1/disable", None),
                               ("post", "/api/admin/accounts/1/sign-out", None),
                               ("put", "/api/admin/accounts/2/coach-ceiling", {"ceiling_usd": 100})):
        r = getattr(friend, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 404, path
    assert TestClient(api.app).get("/api/admin").status_code == 401
    assert admin.get("/api/admin").json()["log"] == []


def test_the_admin_is_matched_by_email_not_by_owning_the_install():
    owner = sign_in()
    admin = sign_in(**{**ADMIN, "email": ADMIN["email"].upper()})
    assert owner.get("/api/admin").status_code == 404
    rows = admin.get("/api/admin").json()["accounts"]
    assert [(r["is_owner"], r["is_admin"]) for r in rows] == [(True, False), (False, True)]


def test_overview_shows_counts_but_never_activities_or_keys(tmp_path):
    admin = sign_in(**ADMIN)
    friend = sign_in(**FRIEND)
    friend.put("/api/settings/source", json={"api_key": "friend-secret-key", "athlete_id": "i99"})
    r = admin.get("/api/admin")
    assert "friend-secret-key" not in r.text and "i99" not in r.text
    body = r.json()
    f = next(a for a in body["accounts"] if a["email"] == FRIEND["email"])
    assert f["sign_in"] == ["password"] and f["connected"] == ["intervals.icu"] and f["activities"] == 0
    assert f["coach"]["ceiling"] == 5.0 and f["coach"]["spent"] == 0 and f["sessions"] == 1
    assert f["last_seen"] is not None
    a = next(a for a in body["accounts"] if a["email"] == ADMIN["email"])
    assert a["is_owner"] and a["coach"]["ceiling"] is None
    s = body["system"]
    assert s["model_version"] and s["data_bytes"] > 0 and s["disk"]["total"] > 0
    assert set(f) >= {"created_at", "disabled_at", "last_sync", "sources", "disk_bytes"}


def test_disable_signs_out_and_blocks_sign_in_until_enabled():
    admin = sign_in(**ADMIN)
    friend = sign_in(**FRIEND)
    assert admin.post("/api/admin/accounts/2/disable").status_code == 200
    assert friend.get("/api/today").status_code == 401
    r = TestClient(api.app).post("/api/auth/login", json=FRIEND)
    assert r.status_code == 403 and "turned off" in r.json()["detail"]
    # A wrong password still reads as a wrong password, so disabling doesn't reveal the account.
    assert TestClient(api.app).post("/api/auth/login", json={**FRIEND, "password": "x" * 12}).status_code == 401
    assert admin.post("/api/admin/accounts/2/enable").status_code == 200
    assert TestClient(api.app).post("/api/auth/login", json=FRIEND).status_code == 200
    # The admin can't lock themself out, and unknown accounts or actions are 404.
    assert admin.post("/api/admin/accounts/1/disable").status_code == 409
    assert admin.post("/api/admin/accounts/99/disable").status_code == 404
    assert admin.post("/api/admin/accounts/2/delete").status_code == 404
    log = admin.get("/api/admin").json()["log"]
    assert [(e["action"], e["target"], e["admin"]) for e in log] == [
        ("enable", FRIEND["email"], ADMIN["email"]), ("disable", FRIEND["email"], ADMIN["email"])]


def test_sign_out_ends_every_session():
    admin = sign_in(**ADMIN)
    friend, friend_phone = sign_in(**FRIEND), sign_in(**FRIEND)
    r = admin.post("/api/admin/accounts/2/sign-out").json()
    assert r["sessions"] == 2
    assert friend.get("/api/today").status_code == 401 and friend_phone.get("/api/today").status_code == 401
    assert TestClient(api.app).post("/api/auth/login", json=FRIEND).status_code == 200


def test_signup_toggle_overrides_the_env(monkeypatch):
    admin = sign_in(**ADMIN)
    monkeypatch.setenv("TALUS_SIGNUP", "closed")
    assert not TestClient(api.app).get("/api/auth/status").json()["signup_open"]
    assert admin.put("/api/admin/signup", json={"open": True}).json() == {"open": True}
    assert TestClient(api.app).post("/api/auth/signup", json=FRIEND).status_code == 200
    admin.put("/api/admin/signup", json={"open": False})
    monkeypatch.setenv("TALUS_SIGNUP", "open")
    assert TestClient(api.app).post("/api/auth/signup", json=OTHER).status_code == 403


def test_coach_ceiling_per_account(monkeypatch):
    monkeypatch.setenv("TALUS_COACH_CAP_USD", "5")
    admin = sign_in(**ADMIN)
    friend = sign_in(**FRIEND)
    assert admin.put("/api/admin/accounts/2/coach-ceiling", json={"ceiling_usd": 12}).status_code == 200
    cap = friend.get("/api/settings").json()["coach"]["cap"]
    assert (cap["ceiling"], cap["limit"]) == (12, 12)
    # The account can still pick a lower cap of its own.
    friend.put("/api/settings/coach", json={"enabled": True, "brief_model": "claude-haiku-5-5",
                                            "analysis_model": "claude-haiku-5-5", "monthly_cap_usd": 3})
    assert friend.get("/api/settings").json()["coach"]["cap"]["limit"] == 3
    admin.put("/api/admin/accounts/2/coach-ceiling", json={"ceiling_usd": None})
    assert friend.get("/api/settings").json()["coach"]["cap"]["ceiling"] == 5
    assert admin.put("/api/admin/accounts/2/coach-ceiling", json={"ceiling_usd": -1}).status_code == 422
    assert admin.put("/api/admin/accounts/1/coach-ceiling", json={"ceiling_usd": 1}).status_code == 409


def test_new_columns_are_added_to_an_existing_accounts_db(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    db = sqlite3.connect(root / "accounts.db")
    db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE COLLATE NOCASE,"
               " password_hash TEXT NOT NULL, is_owner INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    db.execute("INSERT INTO account VALUES (1, 'a@example.com', '', 1, '2026-10-01T00:00:00')")
    db.commit()
    db.close()
    a = Accounts(root).get(1)
    assert a.email == "a@example.com" and a.disabled_at is None and a.coach_ceiling_usd is None


def test_show_ai_cost_flag_adds_each_calls_cost(tmp_path, monkeypatch):
    from tests.conftest import OWNER
    from tests.test_coach import FakeClient, _demo

    monkeypatch.setattr(accounts, "ADMIN_EMAIL", OWNER["email"])
    owner = _demo(tmp_path, monkeypatch)
    fake = FakeClient({"call": "rest", "why": "Rest today."}, {"call": "rest", "why": "Rest today."},
                      {"answer": "Rest is the call.", "used": []})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    assert owner.get("/api/coach/brief").json()["cost"] is None  # off by default
    assert owner.get("/api/admin").json()["features"] == {"show_ai_cost": False}

    assert owner.put("/api/admin/features/show_ai_cost", json={"on": True}).json() == {"show_ai_cost": True}
    assert owner.put("/api/admin/features/nope", json={"on": True}).status_code == 404
    friend = sign_in(**FRIEND)
    assert friend.put("/api/admin/features/show_ai_cost", json={"on": False}).status_code == 404

    # The reused brief cost nothing now and carries its first call's cost: 900 in, 120 out,
    # an unknown model priced as the dearest (Opus 5.5, $4 / $20 per MTok).
    cached = owner.get("/api/coach/brief").json()["cost"]
    assert cached == {"usd": 0.006, "model": "claude-test", "input_tokens": 900, "output_tokens": 120, "cached": True}
    fresh = owner.get("/api/coach/brief?refresh=1").json()["cost"]
    assert fresh["cached"] is False and fresh["usd"] == 0.006

    msgs = owner.post("/api/coach/chat", json={"question": "Should I rest?"}).json()["messages"]
    assert msgs[0]["cost"] is None and msgs[1]["cost"]["usd"] == 0.006
    assert owner.get("/api/coach/chat").json()["messages"][1]["cost"]["cached"] is False
    log = owner.get("/api/admin").json()["log"]
    assert log[0]["action"] == "feature_on" and log[0]["detail"] == {"feature": "show_ai_cost"}

    owner.put("/api/admin/features/show_ai_cost", json={"on": False})
    assert owner.get("/api/coach/brief").json()["cost"] is None
    assert "cost" not in owner.get("/api/coach/chat").json()["messages"][1]
