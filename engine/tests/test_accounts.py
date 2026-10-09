import io
import json
import sqlite3
import zipfile
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from talus import api, cli
from talus.accounts import Accounts, check_password, hash_password
from talus.models import Thresholds
from talus.sources.base import RawFile, RemoteActivity
from talus.store import Store
from tests.conftest import OWNER, sign_in
from tests.test_api import _gpx
from tests.test_coach import FakeClient, _demo

FRIEND = {"email": "friend@example.com", "password": "another long password"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for k in ("INTERVALS_API_KEY", "INTERVALS_ATHLETE_ID", "ANTHROPIC_API_KEY", "GARMIN_EMAIL", "TALUS_SIGNUP",
              "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TALUS_PUBLIC_URL",
              "TALUS_COACH_CAP_USD", "TALUS_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(api, "load_env", lambda: None)
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    api._failures.clear()


def _legacy_install(root):
    """A single-user install from before accounts: talus.db and raw/ straight in the data folder."""
    store = Store(root)
    store.set_thresholds(Thresholds(54, 165, 187, 209))
    start = datetime.combine(date.today() - timedelta(days=1), datetime.min.time()) + timedelta(hours=8)
    act = RemoteActivity("intervals", "i1", start, name="Baldy", sport="Run")
    activity_id = store.add(act, RawFile(_gpx(start), "gpx"))
    cli._score_and_save(store, activity_id, store.raw(store.activity(activity_id)), store.thresholds(), "Run",
                        "Baldy", log=lambda _: None)
    store.db.close()
    return activity_id


def test_passwords_are_salted_scrypt():
    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$") and h != hash_password("correct horse battery")
    assert check_password("correct horse battery", h) and not check_password("wrong", h)


def test_every_data_route_needs_a_session():
    client = TestClient(api.app)
    for path in ("/api/today", "/api/load", "/api/activities", "/api/settings", "/api/account",
                 "/api/account/export", "/api/coach/brief"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/checkin", json={"soreness": 1, "energy": 1, "pain": 1}).status_code == 401
    client.cookies.set(api.COOKIE, "made-up")
    assert client.get("/api/today").status_code == 401
    s = client.get("/api/auth/status").json()
    assert s == {"account": None, "has_accounts": False, "signup_open": True, "existing_data": False,
                 "google": False}


def test_existing_data_goes_to_the_first_account_after_a_backup(tmp_path):
    root = tmp_path / "data"
    activity_id = _legacy_install(root)
    status = TestClient(api.app).get("/api/auth/status").json()
    assert status["existing_data"] and not status["has_accounts"]

    backups = list((root / "backups").glob("talus-before-accounts-*.db"))
    assert len(backups) == 1
    assert sqlite3.connect(backups[0]).execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 1
    assert not (root / "talus.db").exists() and not (root / "raw").exists()
    assert (root / "users" / "1" / "talus.db").exists()

    owner = sign_in()
    me = owner.get("/api/account").json()
    assert me["id"] == 1 and me["is_owner"]
    assert owner.get("/api/settings").json()["thresholds"]["ant"] == 187
    a = owner.get(f"/api/activities/{activity_id}").json()  # raw file still found after the move
    assert a["name"] == "Baldy" and a["distance_mi"] > 0

    # Running again changes nothing: the data is not moved twice.
    Accounts(root)
    assert len(list((root / "backups").glob("*.db"))) == 1


def test_first_account_takes_the_env_key_and_others_never_see_it(monkeypatch):
    monkeypatch.setenv("INTERVALS_API_KEY", "owner-secret-key")
    monkeypatch.setenv("INTERVALS_ATHLETE_ID", "i746070")
    owner = sign_in()
    friend = sign_in(**FRIEND)
    o = owner.get("/api/settings")
    f = friend.get("/api/settings")
    assert o.json()["source"]["key_set"] and o.json()["source"]["athlete_id"] == "i746070"
    assert not f.json()["source"]["key_set"]
    assert "owner-secret-key" not in o.text + f.text
    assert friend.post("/api/sync", json={"days": 7}).status_code == 409


def test_accounts_never_see_each_others_data(tmp_path, monkeypatch):
    owner = _demo(tmp_path, monkeypatch)
    friend = sign_in(**FRIEND)
    acts = owner.get("/api/activities").json()
    assert len(acts) == 30
    assert friend.get("/api/activities").json() == []
    assert friend.get(f"/api/activities/{acts[0]['id']}").status_code == 404
    assert friend.get("/api/settings").json()["thresholds"] is None
    assert friend.get("/api/today").status_code == 409  # no thresholds of their own yet

    friend.put("/api/settings/thresholds", json={"resting": 50, "ant": 160, "max": 175})
    friend.post("/api/checkin", json={"soreness": 5, "energy": 1, "pain": 1})
    friend.put("/api/settings/coach", json={"enabled": False, "brief_model": "claude-haiku-5-5",
                                            "analysis_model": "claude-haiku-5-5"})
    assert owner.get("/api/settings").json()["thresholds"]["ant"] == 172
    assert owner.get("/api/today").json()["checkin"] is None
    assert owner.get("/api/settings").json()["coach"]["enabled"]


def test_saved_keys_are_encrypted_and_never_returned(tmp_path):
    friend = sign_in(**FRIEND)
    assert friend.put("/api/settings/source", json={"api_key": "friend-secret-key", "athlete_id": "i99"}).status_code \
        == 200
    s = friend.get("/api/settings")
    assert s.json()["source"]["key_set"] and s.json()["source"]["kind"] == "api_key"
    assert "friend-secret-key" not in s.text
    raw = (tmp_path / "data" / "accounts.db").read_bytes()
    assert b"friend-secret-key" not in raw
    assert friend.put("/api/settings/source", json={"api_key": "x", "athlete_id": "i99"}).status_code == 422
    assert friend.put("/api/settings/source", json={"api_key": "long-enough-key", "athlete_id": "x; drop"}) \
        .status_code == 422
    friend.delete("/api/settings/source")
    assert not friend.get("/api/settings").json()["source"]["key_set"]


def test_sign_in_out_and_wrong_passwords():
    sign_in()
    client = TestClient(api.app)
    assert client.post("/api/auth/login", json={**OWNER, "password": "nope nope nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={**OWNER, "email": "OWNER@example.com"}).status_code == 200
    assert client.get("/api/auth/status").json()["account"]["email"] == OWNER["email"]
    client.post("/api/auth/logout")
    assert client.get("/api/today").status_code == 401
    assert client.post("/api/auth/signup", json=OWNER).status_code == 422  # email taken
    assert client.post("/api/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_repeated_wrong_passwords_are_slowed():
    sign_in()
    client = TestClient(api.app)
    for _ in range(api.MAX_FAILURES):
        client.post("/api/auth/login", json={**OWNER, "password": "wrong password!"})
    assert client.post("/api/auth/login", json=OWNER).status_code == 429


def test_password_change_signs_out_other_sessions():
    phone = sign_in()
    laptop = sign_in()
    assert laptop.put("/api/account/password", json={"current": "wrong", "new": "a brand new password"}) \
        .status_code == 403
    assert laptop.put("/api/account/password", json={"current": OWNER["password"], "new": "short"}).status_code == 422
    assert laptop.put("/api/account/password",
                      json={"current": OWNER["password"], "new": "a brand new password"}).status_code == 200
    assert laptop.get("/api/account").status_code == 200
    assert phone.get("/api/account").status_code == 401


def test_closed_signup_still_lets_the_first_account_in(monkeypatch):
    monkeypatch.setenv("TALUS_SIGNUP", "closed")
    sign_in()
    r = TestClient(api.app).post("/api/auth/signup", json=FRIEND)
    assert r.status_code == 403


def test_export_holds_the_accounts_data_and_no_keys(tmp_path, monkeypatch):
    owner = _demo(tmp_path, monkeypatch)
    owner.put("/api/settings/source", json={"api_key": "owner-secret-key", "athlete_id": "i1"})
    r = owner.get("/api/account/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "talus.db" in names and "account.json" in names
    assert sum(n.startswith("raw/") for n in names) == 30
    assert all(b"owner-secret-key" not in z.read(n) for n in names)
    exported = tmp_path / "exported.db"
    exported.write_bytes(z.read("talus.db"))
    assert sqlite3.connect(exported).execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 30


def test_delete_removes_the_account_and_its_folder(tmp_path):
    sign_in()
    friend = sign_in(**FRIEND)
    me = friend.get("/api/account").json()
    folder = tmp_path / "data" / "users" / str(me["id"])
    assert folder.exists()
    assert friend.post("/api/account/delete", json={"password": "wrong"}).status_code == 403
    assert friend.post("/api/account/delete", json={"password": FRIEND["password"]}).json() == {"ok": True}
    assert not folder.exists()
    assert TestClient(api.app).post("/api/auth/login", json=FRIEND).status_code == 401
    assert (tmp_path / "data" / "users" / "1").exists()


def test_coach_stops_at_the_monthly_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_COACH_CAP_USD", "1")
    sign_in()
    friend = sign_in(**FRIEND)
    friend.put("/api/settings/thresholds", json={"resting": 50, "ant": 160, "max": 175})
    store = Store(tmp_path / "data" / "users" / "2")
    fake = FakeClient({"call": "rest", "why": "Rest today."})
    monkeypatch.setattr(api, "coach_client", lambda: fake)

    cap = friend.get("/api/settings").json()["coach"]["cap"]
    assert (cap["limit"], cap["ceiling"], cap["spent"]) == (1.0, 1.0, 0.0)
    # Asking for more than the server allows keeps the server ceiling.
    friend.put("/api/settings/coach", json={"enabled": True, "brief_model": "claude-opus-5-5",
                                            "analysis_model": "claude-opus-5-5", "monthly_cap_usd": 50})
    assert friend.get("/api/settings").json()["coach"]["cap"]["limit"] == 1.0

    # $1.20 of Opus already spent this month: 100k in, 40k out.
    store.db.execute(
        "INSERT INTO coach_log (created_at, mode, subject, prompt_version, packet_hash, packet, model, source, reply,"
        " attempts, rejected, usage) VALUES (?, 'brief', 'x', 'v', 'h', '{}', 'claude-opus-5-5', 'coach', '{}', 1,"
        " '[]', ?)", (datetime.now().isoformat(timespec="seconds"),
                      json.dumps({"input_tokens": 100_000, "output_tokens": 40_000})))
    store.db.commit()
    assert friend.get("/api/settings").json()["coach"]["cap"]["spent"] == pytest.approx(1.2)
    b = friend.get("/api/coach/brief").json()
    assert b["source"] == "rules" and b["fallback_reason"] == "cap" and fake.calls == []

    # The owner has no ceiling and has spent nothing, so the coach runs.
    owner_cap = sign_in().get("/api/settings").json()["coach"]["cap"]
    assert owner_cap["ceiling"] is None and owner_cap["limit"] is None


def test_cli_works_on_the_owner_or_a_named_account(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda _: OWNER["password"])
    cli.main(["account", "create", OWNER["email"]])
    monkeypatch.setattr("getpass.getpass", lambda _: FRIEND["password"])
    cli.main(["account", "create", FRIEND["email"]])
    out = capsys.readouterr().out
    assert "Created account 1 for owner@example.com (owner)" in out and "Created account 2" in out
    cli.main(["thresholds", "--resting", "45", "--ant", "172", "--max", "190"])
    cli.main(["--user", FRIEND["email"], "thresholds", "--resting", "50", "--ant", "160", "--max", "175"])
    assert Store(tmp_path / "data" / "users" / "1").thresholds().ant == 172
    assert Store(tmp_path / "data" / "users" / "2").thresholds().ant == 160
    cli.main(["account", "list"])
    assert "friend@example.com" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--user", "nobody@example.com", "list"])


def test_sync_and_its_coach_follow_up_run_as_the_signed_in_account(tmp_path, monkeypatch):
    from tests.test_intervals import source

    sign_in()
    friend = sign_in(**FRIEND)
    friend.put("/api/settings/source", json={"api_key": "friend-secret-key", "athlete_id": "i42"})
    monkeypatch.setattr("talus.sources.intervals.IntervalsSource.from_credential", lambda cred: source())
    seen = []
    monkeypatch.setattr(api, "warm_coach", lambda ids: seen.append((api._account.get().email, len(ids))))
    days = (date.today() - date(2026, 9, 1)).days
    assert friend.post("/api/sync", json={"days": days}).json()["saved"] == 2
    assert seen == [(FRIEND["email"], 2)]  # the background coach run still knows whose data it is
    assert len(Store(tmp_path / "data" / "users" / "2").activities()) == 2
    assert Store(tmp_path / "data" / "users" / "1").activities() == []


# Sign in with Google

GOOGLE = {"GOOGLE_CLIENT_ID": "talus-test.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "shh"}


def _google(monkeypatch, sub="g-123", email=OWNER["email"], verified=True, **overrides):
    """Patch the token exchange to return these ID-token claims, echoing the flow's nonce."""
    for k, v in GOOGLE.items():
        monkeypatch.setenv(k, v)
    from talus import google_auth

    def claims(code):
        assert code == "the-code"
        return {"iss": "https://accounts.google.com", "aud": GOOGLE["GOOGLE_CLIENT_ID"], "sub": sub, "email": email,
                "email_verified": verified, "exp": datetime.now().timestamp() + 600, "nonce": _google.nonce,
                **overrides}
    monkeypatch.setattr(google_auth, "exchange_code", claims)


def _google_round_trip(client, path="/api/auth/google/start", code="the-code"):
    from urllib.parse import parse_qs, urlparse

    start = client.get(path, follow_redirects=False)
    assert start.status_code == 303
    q = parse_qs(urlparse(start.headers["location"]).query)
    assert q["redirect_uri"] == ["http://localhost:3000/api/auth/google/callback"]
    _google.nonce = q["nonce"][0]
    return client.get(f"/api/auth/google/callback?code={code}&state={q['state'][0]}", follow_redirects=False)


def test_google_button_only_when_configured(monkeypatch):
    assert TestClient(api.app).get("/api/auth/status").json()["google"] is False
    r = TestClient(api.app).get("/api/auth/google/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?google_error=")
    _google(monkeypatch)
    assert TestClient(api.app).get("/api/auth/status").json()["google"] is True


def test_google_signs_into_the_account_with_that_email_and_links_it(monkeypatch):
    sign_in()
    _google(monkeypatch)
    client = TestClient(api.app)
    r = _google_round_trip(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = client.get("/api/account").json()
    assert me["id"] == 1 and me["google"]["email"] == OWNER["email"] and me["has_password"]
    # Next time the Google id itself finds the account, even if the Google email changed.
    _google(monkeypatch, email="renamed@gmail.com")
    other = TestClient(api.app)
    _google_round_trip(other)
    assert other.get("/api/account").json()["id"] == 1


def test_google_makes_a_new_passwordless_account(monkeypatch):
    sign_in()
    _google(monkeypatch, sub="g-new", email="new@gmail.com")
    client = TestClient(api.app)
    _google_round_trip(client, "/api/auth/google/start?next=/load")
    me = client.get("/api/account").json()
    assert me["email"] == "new@gmail.com" and not me["is_owner"] and not me["has_password"]
    assert TestClient(api.app).post("/api/auth/login", json={"email": "new@gmail.com", "password": ""}).status_code \
        == 401
    assert client.post("/api/account/google/unlink").status_code == 409  # would lock them out
    assert client.put("/api/account/password", json={"current": "", "new": "now with a password"}).status_code == 200
    assert client.post("/api/account/google/unlink").json() == {"ok": True}
    assert client.get("/api/account").json()["google"] is None


def test_google_respects_closed_signup_and_bad_tokens(monkeypatch):
    sign_in()
    monkeypatch.setenv("TALUS_SIGNUP", "closed")
    _google(monkeypatch, sub="g-x", email="stranger@gmail.com")
    r = _google_round_trip(TestClient(api.app))
    assert "google_error=" in r.headers["location"] and "closed" in r.headers["location"]
    for bad in ({"verified": False}, {"aud": "someone-else"}, {"iss": "https://evil.example"},
                {"nonce": "replayed"}, {"exp": 1}):
        _google(monkeypatch, **bad)
        client = TestClient(api.app)
        r = _google_round_trip(client)
        assert r.headers["location"].startswith("/login?google_error="), bad
        assert client.get("/api/account").status_code == 401


def test_google_callback_needs_the_browser_that_started_it(monkeypatch):
    _google(monkeypatch)
    started = TestClient(api.app)
    start = started.get("/api/auth/google/start", follow_redirects=False)
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    stranger = TestClient(api.app)
    r = stranger.get(f"/api/auth/google/callback?code=the-code&state={state}", follow_redirects=False)
    assert "google_error=" in r.headers["location"] and stranger.get("/api/account").status_code == 401
    r = started.get("/api/auth/google/callback?code=the-code&state=wrong", follow_redirects=False)
    assert "google_error=" in r.headers["location"]


def test_link_google_from_settings(monkeypatch):
    sign_in()
    friend = sign_in(**FRIEND)
    _google(monkeypatch, sub="g-friend", email="friend.personal@gmail.com")
    r = _google_round_trip(friend, "/api/auth/google/start?link=1")
    assert r.headers["location"] == "/settings"
    assert friend.get("/api/account").json()["google"]["email"] == "friend.personal@gmail.com"
    # The same Google account can't also link to the owner.
    owner = sign_in()
    r = _google_round_trip(owner, "/api/auth/google/start?link=1")
    assert "already+linked" in r.headers["location"]


def test_a_fresh_install_does_not_claim_existing_data(tmp_path):
    Store(tmp_path / "data" / "users" / "1")  # opened before anyone signs up, still empty
    assert TestClient(api.app).get("/api/auth/status").json()["existing_data"] is False
