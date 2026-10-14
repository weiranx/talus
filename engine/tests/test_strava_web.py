import gzip
import json
import stat
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from talus import api, cli
from talus.accounts import Accounts
from talus.models import Thresholds
from talus.sources.base import RawFile, RemoteActivity
from talus.sources.strava_web import puller
from talus.sources.strava_web.client import (StravaCaptchaError, StravaLoginError, StravaWebClient,
                                             cookie_path)
from talus.store import Store
from tests.test_api import _gpx

FIXTURES = Path(__file__).parent / "fixtures" / "strava_web"
HTML = {"content-type": "text/html; charset=utf-8"}
EMAIL, PASSWORD = "me@example.com", "hunter22"


class FakeStrava:
    """strava.com as far as the client sees it: a sign-in form, a session cookie, pages that send
    you to /login without one, and export_original."""

    def __init__(self, captcha=False, password=PASSWORD, export=None):
        self.captcha, self.password = captcha, password
        self.export = export if export is not None else gzip.compress(_gpx(datetime(2026, 10, 1, 7)))
        self.requests: list[httpx.Request] = []
        self.live: set[str] = set()  # session cookie values Strava accepts; each sign-in adds one
        self.refuse = 302  # what a request without a live session gets: sent to /login, or 401/403

    def signed_in(self, request):
        return httpx.Cookies(dict(_cookies(request))).get("_strava4_session") in self.live

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/login":
            page = "login_captcha.html" if self.captcha else "login.html"
            return httpx.Response(200, headers={**HTML, "set-cookie": "_strava4_session=anon; Path=/"},
                                  text=(FIXTURES / page).read_text())
        if path == "/session" and request.method == "POST":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            if form.get("authenticity_token") == "tok-123" and form.get("password") == self.password:
                value = f"s{len(self.live) + 1}"
                self.live.add(value)
                return httpx.Response(302, headers={"location": "https://www.strava.com/dashboard",
                                                    "set-cookie": f"_strava4_session={value}; Path=/; Secure"})
            return httpx.Response(302, headers={"location": "https://www.strava.com/login"})
        if not self.signed_in(request):
            if self.refuse != 302:
                return httpx.Response(self.refuse)
            return httpx.Response(302, headers={"location": "https://www.strava.com/login"})
        if path == "/dashboard":
            return httpx.Response(200, headers=HTML, text=(FIXTURES / "dashboard.html").read_text())
        if path == "/activities/1001/export_original":
            return httpx.Response(302, headers={"location": "/activities/1001/export_original/file"})
        if path == "/activities/1001/export_original/file":
            return httpx.Response(200, content=self.export)
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def paths(self):
        return [f"{r.method} {r.url.path}" for r in self.requests]


def _cookies(request):
    for part in request.headers.get("cookie", "").split(";"):
        if "=" in part:
            yield part.strip().split("=", 1)


class Clock:
    def __init__(self):
        self.t, self.slept = 100.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def client(tmp_path, fake, clock=None, **kwargs):
    clock = clock or Clock()
    kwargs.setdefault("email", EMAIL)
    kwargs.setdefault("password", PASSWORD)
    return StravaWebClient(cookie_path(tmp_path), transport=httpx.MockTransport(fake), clock=clock,
                           sleep=clock.sleep, **kwargs)


# Session client

def test_login_reads_the_token_then_posts_the_session(tmp_path):
    fake = FakeStrava()
    resp = client(tmp_path, fake).get("/dashboard")
    assert resp.status_code == 200 and "Dashboard" in resp.text
    assert fake.paths() == ["GET /login", "POST /session", "GET /dashboard"]
    post = fake.requests[1]
    form = post.content.decode()
    assert "authenticity_token=tok-123" in form and "email=me%40example.com" in form
    assert post.headers["x-csrf-token"] == "csrf-from-login"
    assert "Chrome/" in post.headers["user-agent"]


def test_cookies_are_kept_0600_and_reused_without_signing_in(tmp_path):
    fake = FakeStrava()
    client(tmp_path, fake).get("/dashboard")
    jar = cookie_path(tmp_path)
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600
    saved = json.loads(jar.read_text())
    assert {"name": "_strava4_session", "value": "s1"}.items() <= saved["cookies"][0].items()
    assert saved["csrf"] == "csrf-from-dashboard"

    again = FakeStrava()
    again.live = {"s1"}
    c = client(tmp_path, again, password=None)  # no password needed while the session lives
    assert c.get("/dashboard").status_code == 200
    assert again.paths() == ["GET /dashboard"] and c.logins == 0


def test_a_loose_cookie_file_is_tightened(tmp_path):
    jar = cookie_path(tmp_path)
    jar.write_text(json.dumps({"csrf": None, "cookies": []}))
    jar.chmod(0o644)
    client(tmp_path, FakeStrava())
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600


@pytest.mark.parametrize("refuse", [302, 401, 403])
def test_signs_in_again_only_when_sent_to_login_or_refused(tmp_path, refuse):
    fake = FakeStrava()
    client(tmp_path, fake).get("/dashboard")
    fake.live.clear()  # the saved session has ended
    fake.refuse = refuse
    fake.requests.clear()
    c = client(tmp_path, fake)
    assert c.get("/dashboard").status_code == 200
    assert c.logins == 1
    assert fake.paths() == ["GET /dashboard", "GET /login", "POST /session", "GET /dashboard"]


def test_a_session_refused_right_after_signing_in_stops(tmp_path):
    fake = FakeStrava()
    fake.refuse = 403
    fake.signed_in = lambda request: False
    with pytest.raises(StravaLoginError):
        client(tmp_path, fake).get("/dashboard")
    assert fake.paths().count("POST /session") == 1


def test_a_refused_sign_in_stops_without_retrying(tmp_path):
    fake = FakeStrava(password="something else")
    with pytest.raises(StravaLoginError) as e:
        client(tmp_path, fake).get("/dashboard")
    assert e.value.code == "login_failed"
    assert fake.paths().count("POST /session") == 1


def test_a_captcha_stops_before_the_password_is_sent(tmp_path):
    fake = FakeStrava(captcha=True)
    with pytest.raises(StravaCaptchaError) as e:
        client(tmp_path, fake).get("/dashboard")
    assert e.value.code == "captcha"
    assert fake.paths() == ["GET /login"]


def test_an_invisible_captcha_script_is_not_a_challenge(tmp_path):
    # dashboard.html loads reCAPTCHA's script, as signed-in pages may; only a challenge stops a run.
    assert client(tmp_path, FakeStrava()).get("/dashboard").status_code == 200


def test_no_password_and_no_session_says_what_to_set(tmp_path):
    with pytest.raises(StravaLoginError, match="STRAVA_EMAIL and STRAVA_PASSWORD"):
        client(tmp_path, FakeStrava(), email=None, password=None).get("/dashboard")


def test_session_cookie_override_skips_sign_in(tmp_path):
    fake = FakeStrava()
    fake.live = {"good"}
    c = client(tmp_path, fake, email=None, password=None, session_cookie="_strava4_session=good; sp=abc")
    assert c.get("/dashboard").status_code == 200
    assert fake.paths() == ["GET /dashboard"]
    assert "sp=abc" in fake.requests[0].headers["cookie"]


def test_csrf_token_goes_on_writes_only(tmp_path):
    fake = FakeStrava()
    c = client(tmp_path, fake)
    c.get("/dashboard")
    c.request("POST", "/athlete/thing", data={"x": "1"})
    get, post = fake.requests[-2], fake.requests[-1]
    assert "x-csrf-token" not in get.headers
    assert post.headers["x-csrf-token"] == "csrf-from-dashboard"


def test_one_request_a_second(tmp_path):
    clock = Clock()
    fake = FakeStrava()
    c = client(tmp_path, fake, clock=clock)
    c.get("/dashboard")
    c.get("/dashboard")
    assert len(fake.requests) == 4 and len(clock.slept) == 3  # every request after the first waits
    assert all(s >= 1.0 for s in clock.slept)


# Puller

def test_export_original_is_unwrapped_and_sniffed(tmp_path):
    src = puller.StravaWebSource(client(tmp_path, FakeStrava()))
    raw = src.fetch(RemoteActivity("strava_web", "1001", datetime(2026, 10, 1, 7)))
    assert raw.fmt == "gpx" and raw.data.startswith(b"<gpx")


def test_activity_list_is_a_stub_until_the_har_maps_it(tmp_path):
    src = puller.StravaWebSource(client(tmp_path, FakeStrava()))
    with pytest.raises(puller.StravaNotMapped):
        src.list_activities(date(2026, 9, 1))


class FakeSource:
    name = puller.SOURCE

    def __init__(self, acts, metrics=None):
        self.acts, self.fetched = acts, []
        self.client = None
        self._metrics = metrics or {}

    def list_activities(self, since, until=None):
        return self.acts

    def fetch(self, act):
        self.fetched.append(act.source_id)
        return RawFile(_gpx(act.start_time), "gpx")

    def metrics(self, act):
        return self._metrics.get(act.source_id, {})


def _store(tmp_path) -> Store:
    store = Store(tmp_path / "user")
    store.set_thresholds(Thresholds(45, 150, 172, 190))
    at = datetime(2026, 10, 1, 7)
    store.add(RemoteActivity("healthkit", "hk-1", at.replace(second=30), distance_m=10010), RawFile(b"<tcx/>", "tcx"))
    store.add(RemoteActivity("intervals", "i-1", at, distance_m=10000), RawFile(b"fit", "fit"))
    return store


def test_a_close_match_becomes_an_alias_of_the_fit_record_and_is_never_downloaded(tmp_path):
    store = _store(tmp_path)
    fit_id = store.db.execute("SELECT id FROM activity WHERE source = 'intervals'").fetchone()[0]
    src = FakeSource([RemoteActivity("strava_web", "s-1", datetime(2026, 10, 1, 7, 0, 20), distance_m=10050)],
                     {"s-1": {"relative_effort": 87.0}})
    result = puller.sync(store, date(2026, 9, 1), log=lambda _: None, source=src)
    assert result["aliased"] == 1 and result["saved"] == 0 and src.fetched == []
    assert store.aliases(fit_id) == {"strava_web": "s-1"}
    assert store.has("strava_web", "s-1")
    assert store.external_metrics(fit_id, "strava") == {"relative_effort": 87.0}
    assert store.db.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 2


@pytest.mark.parametrize("start, distance", [
    (datetime(2026, 10, 1, 7, 1, 45), 10000),  # 75 s and 105 s from the two records: past 60 s
    (datetime(2026, 10, 1, 7, 0, 10), 10250),  # 2.5% longer: past 1%
    (datetime(2026, 10, 1, 7, 0, 10), None),   # no distance to match on
])
def test_near_but_not_the_same_record_is_skipped_not_counted_twice(tmp_path, start, distance):
    store = _store(tmp_path)
    src = FakeSource([RemoteActivity("strava_web", "s-2", start, distance_m=distance)])
    result = puller.sync(store, date(2026, 9, 1), log=lambda _: None, source=src)
    assert (result["aliased"], result["duplicates"], result["saved"]) == (0, 1, 0) and src.fetched == []


def test_a_new_activity_is_downloaded_kept_and_scored_once(tmp_path):
    store = _store(tmp_path)
    act = RemoteActivity("strava_web", "s-3", datetime(2026, 10, 3, 9), name="Baldy", sport="running",
                         distance_m=5400)
    src = FakeSource([act], {"s-3": {"relative_effort": 120.0, "fitness": 41.0, "freshness": -6.0}})
    result = puller.sync(store, date(2026, 9, 1), log=lambda _: None, source=src)
    assert result["saved"] == 1 and src.fetched == ["s-3"]
    row = store.db.execute("SELECT * FROM activity WHERE source = 'strava_web'").fetchone()
    assert row["raw_path"] == "raw/strava_web/s-3.gpx" and (store.dir / row["raw_path"]).is_file()
    assert store.db.execute("SELECT 1 FROM activity_score WHERE activity_id = ?", (row["id"],)).fetchone()
    assert store.external_metrics(row["id"], "strava")["freshness"] == -6.0
    again = puller.sync(store, date(2026, 9, 1), log=lambda _: None, source=src)
    assert again["saved"] == 0 and src.fetched == ["s-3"]
    assert store.settings()["last_sync_strava_web"]["listed"] == 1
    assert "last_sync" not in store.settings()  # never shown as the account's sync


def test_a_stopped_sync_is_recorded_with_its_code(tmp_path):
    store = Store(tmp_path / "user")
    src = puller.StravaWebSource(client(tmp_path, FakeStrava(captcha=True)))
    src.list_activities = lambda since: src.client.get("/dashboard")
    with pytest.raises(StravaCaptchaError):
        puller.sync(store, date(2026, 9, 1), log=lambda _: None, source=src)
    assert store.settings()["last_sync_strava_web"]["error_code"] == "captcha"


# Gating and scheduling

def test_only_the_owner_with_the_flag_gets_it_and_only_after_intervals(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print: calls.append(store.dir) or {"added_ids": []})
    accounts = Accounts(tmp_path / "data")
    owner = accounts.create("owner@example.com", "correct horse battery")
    other = accounts.create("friend@example.com", "correct horse battery")
    store = Store(accounts.user_dir(owner.id))
    since = date(2026, 9, 1)
    assert cli.strava_web_after(accounts, owner, store, "intervals", since) is None  # off by default
    accounts.set_strava_web_enabled(other.id, True)
    assert cli.strava_web_after(accounts, other, store, "intervals", since) is None  # never another account
    accounts.set_strava_web_enabled(owner.id, True)
    assert cli.strava_web_after(accounts, owner, store, "garmin", since) is None
    assert cli.strava_web_after(accounts, owner, store, "intervals", since) == {"added_ids": []}
    assert calls == [store.dir]


def test_a_strava_failure_never_breaks_the_intervals_sync(tmp_path, monkeypatch):
    accounts = Accounts(tmp_path / "data")
    owner = accounts.create("owner@example.com", "correct horse battery")
    accounts.set_strava_web_enabled(owner.id, True)
    lines = []
    store = Store(accounts.user_dir(owner.id))
    # The real list step is the stub, so this is what a run does today.
    monkeypatch.delenv("STRAVA_SESSION_COOKIE", raising=False)
    monkeypatch.delenv("STRAVA_PASSWORD", raising=False)
    assert cli.strava_web_after(accounts, owner, store, "intervals", date(2026, 9, 1), log=lines.append) is None
    assert "not synced" in lines[-1]
    assert store.settings()["last_sync_strava_web"]["error_code"] == "not_mapped"


def test_cli_sync_runs_it_after_intervals_even_when_intervals_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTERVALS_API_KEY", "k")
    accounts = Accounts(tmp_path / "data")
    accounts.set_strava_web_enabled(accounts.create("owner@example.com", "correct horse battery").id, True)
    order = []

    def broken(*a, **k):
        order.append("intervals")
        raise RuntimeError("Garmin data blocked")

    monkeypatch.setattr(cli, "sync", broken)
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print: order.append("strava") or {"added_ids": []})
    with pytest.raises(RuntimeError, match="blocked"):
        cli.main(["sync", "--no-coach"])
    assert order == ["intervals", "strava"]


def test_sync_now_queues_it_after_the_response(tmp_path, monkeypatch):
    from tests.conftest import sign_in
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTERVALS_API_KEY", "k")
    c = sign_in()
    api.get_accounts().set_strava_web_enabled(api.get_accounts().owner().id, True)
    calls = []
    monkeypatch.setattr(cli, "sync", lambda *a, **k: {"added_ids": [], "saved": 0})
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print: calls.append(since) or {"added_ids": []})
    assert c.post("/api/sync", json={"days": 7}).status_code == 200
    assert len(calls) == 1
    assert c.post("/api/sync", json={"days": 7, "source": "garmin"}).status_code in (403, 409)
    assert len(calls) == 1


# The coach never sees Strava's numbers

SENTINEL = 424242.5


def test_external_metrics_never_reach_the_coach(tmp_path, monkeypatch):
    from tests.test_coach import _demo
    c = _demo(tmp_path, monkeypatch)
    store = api.get_store()
    ids = [r["id"] for r in store.activities()]
    for i in ids:
        store.save_external_metrics(i, "strava", {"relative_effort": SENTINEL, "fitness": SENTINEL,
                                                  "freshness": SENTINEL})
    packets = []
    real = api._coach

    def capture(store, mode, subject, packet, fallback, refresh):
        packets.append((mode, json.dumps(packet, default=str)))
        return real(store, mode, subject, packet, fallback, refresh)

    monkeypatch.setattr(api, "_coach", capture)
    c.get("/api/coach/brief")
    c.get("/api/coach/load")
    for i in ids[:3]:
        c.get(f"/api/coach/activity/{i}")
    c.post("/api/coach/chat", json={"question": "How was my week?"})
    assert {m for m, _ in packets} >= {"brief", "load", "analysis", "chat"}
    for mode, text in packets:
        assert str(SENTINEL) not in text and "424242" not in text, mode
        assert "relative_effort" not in text and "external" not in text, mode


def test_only_the_store_and_the_strava_puller_touch_external_metrics():
    root = Path(api.__file__).parent
    users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if "external_metrics" in p.read_text())
    assert users == ["sources/strava_web/puller.py", "store.py"]
