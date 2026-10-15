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
from talus.sources.strava_web.client import (StravaCaptchaError, StravaSessionExpired, StravaWebClient,
                                             StravaWebError, clean_cookie_header, cookie_path)
from talus.store import Store
from tests.test_api import _gpx

FIXTURES = Path(__file__).parent / "fixtures" / "strava_web"
HTML = {"content-type": "text/html; charset=utf-8"}
LIST = json.loads((FIXTURES / "training_activities.json").read_text())


class FakeStrava:
    """strava.com as far as the client sees it: pages that send you to /login (or answer 401/403)
    without a live session cookie, the My Activities list in pages, and export_original. A
    signed-in page refreshes the session cookie, as Strava's do."""

    def __init__(self, models=None, per_page=20, captcha=False):
        self.models = LIST["models"] if models is None else models
        self.per_page, self.captcha = per_page, captcha
        self.requests: list[httpx.Request] = []
        self.live = {"good", "fresh"}  # session cookie values Strava accepts; it hands out "fresh"
        self.refuse = 302  # without a live session: sent to /login, or 401/403

    def session(self, request):
        return dict(part.strip().split("=", 1) for part in request.headers.get("cookie", "").split(";")
                    if "=" in part).get("_strava4_session")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.captcha:
            return httpx.Response(200, headers=HTML, text=(FIXTURES / "captcha.html").read_text())
        if self.session(request) not in self.live:
            if self.refuse != 302:
                return httpx.Response(self.refuse)
            return httpx.Response(302, headers={"location": "https://www.strava.com/login"})
        refreshed = {"set-cookie": "_strava4_session=fresh; Path=/; Secure"}
        if path == "/dashboard":
            return httpx.Response(200, headers={**HTML, **refreshed}, text=(FIXTURES / "dashboard.html").read_text())
        if path == "/athlete/training_activities":
            assert request.headers["x-requested-with"] == "XMLHttpRequest"
            page = int(request.url.params["page"])
            chunk = self.models[(page - 1) * self.per_page:page * self.per_page]
            return httpx.Response(200, headers={"content-type": "text/javascript; charset=utf-8", **refreshed},
                                  json={"models": chunk, "page": page, "perPage": self.per_page,
                                        "total": len(self.models)})
        if path.endswith("/export_original"):
            return httpx.Response(200, content=gzip.compress(_gpx(datetime(2026, 10, 1, 7))))
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def paths(self):
        return [f"{r.method} {r.url.path}" for r in self.requests]


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
    return StravaWebClient(cookie_path(tmp_path), transport=httpx.MockTransport(fake), clock=clock,
                           sleep=clock.sleep, **kwargs)


# Session client: a borrowed cookie, never a password

def test_a_pasted_cookie_header_is_cleaned():
    assert clean_cookie_header("Cookie: _strava4_session=good;\n sp=abc; junk ;") == "_strava4_session=good; sp=abc"
    assert clean_cookie_header("  ") == ""


def test_no_session_stops_before_any_request(tmp_path):
    fake = FakeStrava()
    with pytest.raises(StravaSessionExpired, match="talus strava-web setup") as e:
        client(tmp_path, fake).get("/dashboard")
    assert e.value.code == "session_expired" and fake.requests == []


def test_the_saved_cookie_signs_in_and_refreshed_cookies_are_kept_0600(tmp_path):
    fake = FakeStrava()
    c = client(tmp_path, fake, saved_cookie="_strava4_session=good; sp=abc")
    assert c.get("/dashboard").status_code == 200
    assert "sp=abc" in fake.requests[0].headers["cookie"]
    jar = cookie_path(tmp_path)
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600
    saved = {x["name"]: x["value"] for x in json.loads(jar.read_text())["cookies"]}
    assert saved["_strava4_session"] == "fresh"  # what Strava set, not what was pasted
    assert json.loads(jar.read_text())["csrf"] == "csrf-from-dashboard"


def test_the_jar_wins_over_the_saved_cookie_and_the_env_cookie_wins_over_both(tmp_path):
    client(tmp_path, FakeStrava(), saved_cookie="_strava4_session=good").get("/dashboard")
    fake = FakeStrava()
    client(tmp_path, fake, saved_cookie="_strava4_session=stale").get("/dashboard")
    assert "_strava4_session=fresh" in fake.requests[0].headers["cookie"]
    fake = FakeStrava()
    client(tmp_path, fake, session_cookie="_strava4_session=good; from=env",
           saved_cookie="_strava4_session=stale").get("/dashboard")
    assert "from=env" in fake.requests[0].headers["cookie"]


def test_a_loose_cookie_file_is_tightened(tmp_path):
    jar = cookie_path(tmp_path)
    jar.write_text(json.dumps({"csrf": None, "cookies": []}))
    jar.chmod(0o644)
    client(tmp_path, FakeStrava())
    assert stat.S_IMODE(jar.stat().st_mode) == 0o600


@pytest.mark.parametrize("refuse", [302, 401, 403])
def test_an_ended_session_stops_and_never_signs_in(tmp_path, refuse):
    fake = FakeStrava()
    fake.refuse = refuse
    with pytest.raises(StravaSessionExpired, match=f"\\({refuse}\\)"):
        client(tmp_path, fake, saved_cookie="_strava4_session=ended").get("/dashboard")
    assert fake.paths() == ["GET /dashboard"]  # no /login, no /session


def test_a_captcha_stops_the_run(tmp_path):
    fake = FakeStrava(captcha=True)
    with pytest.raises(StravaCaptchaError) as e:
        client(tmp_path, fake, saved_cookie="_strava4_session=good").get("/dashboard")
    assert e.value.code == "captcha" and len(fake.requests) == 1


def test_an_invisible_captcha_script_is_not_a_challenge(tmp_path):
    # dashboard.html loads reCAPTCHA's script, as signed-in pages may; only a challenge stops a run.
    assert client(tmp_path, FakeStrava(), saved_cookie="_strava4_session=good").get("/dashboard").status_code == 200


def test_csrf_token_goes_on_writes_only(tmp_path):
    fake = FakeStrava()
    c = client(tmp_path, fake, saved_cookie="_strava4_session=good")
    c.get("/dashboard")
    c.request("POST", "/athlete/thing", data={"x": "1"})
    get, post = fake.requests
    assert "x-csrf-token" not in get.headers
    assert post.headers["x-csrf-token"] == "csrf-from-dashboard"


def test_one_request_a_second(tmp_path):
    clock = Clock()
    fake = FakeStrava()
    c = client(tmp_path, fake, clock=clock, saved_cookie="_strava4_session=good")
    for _ in range(3):
        c.get("/dashboard")
    assert len(fake.requests) == 3 and clock.slept == [1.0, 1.0]


# The activity list and the original file

def _models(n):
    """n activities, a day apart, newest first, in the captured list's shape."""
    base = LIST["models"][0]
    out = []
    for k in range(n):
        local = datetime(2026, 10, 10, 7) - (datetime(2026, 10, 2) - datetime(2026, 10, 1)) * k
        out.append(dict(base, id=8000000000 + n - k, id_str=str(8000000000 + n - k), name=f"Run {k}",
                        start_date_local_raw=int((local - datetime(1970, 1, 1)).total_seconds()),
                        start_time=f"{local:%Y-%m-%d}T14:00:00+0000", suffer_score=None))
    return out


def test_the_list_reads_local_start_distance_and_relative_effort(tmp_path):
    src = puller.StravaWebSource(client(tmp_path, FakeStrava(), saved_cookie="_strava4_session=good"))
    acts = src.list_activities(date(2026, 9, 1))
    assert [a.source_id for a in acts] == ["9000000003", "9000000002"]  # the August run is before `since`
    trail = acts[0]
    assert trail.start_time == datetime(2026, 10, 3, 16, 5, 16)  # local, from start_date_local_raw
    assert (trail.distance_m, trail.gain_m, trail.sport) == (11284.8, 543, "TrailRun")
    assert src.metrics(trail) == {} and src.metrics(acts[1]) == {"relative_effort": 41.0}


def test_the_list_pages_back_to_since_and_stops(tmp_path):
    fake = FakeStrava(models=_models(50))
    src = puller.StravaWebSource(client(tmp_path, fake, saved_cookie="_strava4_session=good"))
    acts = src.list_activities(date(2026, 9, 15))  # 26 days: two pages of 20
    assert len(acts) == 26 and acts[-1].start_time.date() == date(2026, 9, 15)
    assert [r.url.params["page"] for r in fake.requests] == ["1", "2"]
    everything = puller.StravaWebSource(client(tmp_path, FakeStrava(models=_models(50)),
                                               saved_cookie="_strava4_session=good"))
    assert len(everything.list_activities(date(2020, 1, 1))) == 50  # the last, short page ends it


def test_a_list_that_isnt_json_stops_with_a_clear_error(tmp_path):
    def html(request):
        return httpx.Response(200, headers=HTML, text="<html>new site</html>")
    src = puller.StravaWebSource(client(tmp_path, html, saved_cookie="_strava4_session=good"))
    with pytest.raises(StravaWebError, match="website may have changed"):
        src.list_activities(date(2026, 9, 1))


def test_export_original_is_unwrapped_and_sniffed(tmp_path):
    src = puller.StravaWebSource(client(tmp_path, FakeStrava(), saved_cookie="_strava4_session=good"))
    raw = src.fetch(RemoteActivity("strava_web", "1001", datetime(2026, 10, 1, 7)))
    assert raw.fmt == "gpx" and raw.data.startswith(b"<gpx")


# Guided setup: talus strava-web setup / status / off

@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("STRAVA_SESSION_COOKIE", raising=False)
    accounts = Accounts(tmp_path / "data")
    return accounts, accounts.create("owner@example.com", "correct horse battery")


def _setup(monkeypatch, fake, pasted, answer="n"):
    real = puller.open_client
    monkeypatch.setattr(puller, "open_client",
                        lambda folder, *a, **k: real(folder, *a, transport=httpx.MockTransport(fake),
                                                     clock=Clock(), sleep=lambda s: None, **k))
    monkeypatch.setattr(cli, "_paste_cookie", lambda: pasted)
    monkeypatch.setattr(cli, "_ask", lambda prompt="": answer)


def test_setup_checks_the_cookie_saves_it_encrypted_and_turns_it_on(owner, monkeypatch, capsys):
    accounts, account = owner
    _setup(monkeypatch, FakeStrava(), "Cookie: _strava4_session=good; sp=abc")
    cli.main(["strava-web", "setup"])
    out = capsys.readouterr().out
    assert "terms" in out and "DevTools" in out
    assert '3 activities on Strava, the latest Sat Oct 3 "Afternoon Trail Run"' in out
    assert accounts.credential(account.id, "strava_web")["cookie"] == "_strava4_session=good; sp=abc"
    assert b"sp=abc" not in (Path(accounts.root) / "accounts.db").read_bytes()  # encrypted at rest
    assert accounts.strava_web_enabled(account.id)
    assert not cookie_path(accounts.user_dir(account.id)).exists()  # no jar until a sync


def test_setup_with_a_cookie_that_doesnt_work_saves_nothing(owner, monkeypatch, capsys):
    accounts, account = owner
    _setup(monkeypatch, FakeStrava(), "_strava4_session=ended")
    with pytest.raises(SystemExit, match="didn't work.*session has ended"):
        cli.main(["strava-web", "setup"])
    assert accounts.credential(account.id, "strava_web") is None
    assert not accounts.strava_web_enabled(account.id)
    assert list(accounts.user_dir(account.id).glob("strava_web_cookies*")) == []


def test_setup_can_pull_the_last_30_days_and_status_reports_it(owner, monkeypatch, capsys):
    accounts, account = owner
    calls = []
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print, **k: calls.append((since, k)) or
                        {"added_ids": []})
    _setup(monkeypatch, FakeStrava(), "_strava4_session=good", answer="y")
    cli.main(["strava-web", "setup"])
    assert calls == [(date.today() - (date(2026, 1, 31) - date(2026, 1, 1)),
                      {"saved_cookie": "_strava4_session=good"})]
    Store(accounts.user_dir(account.id)).save_settings({"last_sync_strava_web": {
        "at": "2026-10-14T08:00:00", "saved": 2, "aliased": 5, "error": None}})
    capsys.readouterr()
    cli.main(["strava-web", "status"])
    out = capsys.readouterr().out
    assert "In each sync: on" in out and "Session: saved" in out and "2 new, 5 already here" in out


def test_setup_reads_a_piped_cookie_whole(monkeypatch):
    long = "_strava4_session=" + "x" * 3000 + "; sp=abc"  # past a terminal line's 1,024 characters
    monkeypatch.setattr(cli.sys, "stdin", __import__("io").StringIO(long + "\n"))
    assert clean_cookie_header(cli._paste_cookie()) == long
    assert cli._ask("Pull? ") == ""  # no terminal to answer: no


def test_off_forgets_the_session(owner, monkeypatch):
    accounts, account = owner
    accounts.set_credential(account.id, "strava_web", "cookie", {"cookie": "_strava4_session=good"})
    accounts.set_strava_web_enabled(account.id, True)
    jar = cookie_path(accounts.user_dir(account.id))
    jar.parent.mkdir(parents=True, exist_ok=True)
    jar.write_text("{}")
    cli.main(["strava-web", "off"])
    assert accounts.credential(account.id, "strava_web") is None and not jar.exists()
    assert not accounts.strava_web_enabled(account.id)


def test_setup_is_for_the_owner_only(owner, monkeypatch):
    accounts, _ = owner
    accounts.create("friend@example.com", "correct horse battery")
    with pytest.raises(SystemExit, match="owner account only"):
        cli.main(["--user", "friend@example.com", "strava-web", "setup"])


# Puller: matching and saving

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
    src = puller.StravaWebSource(client(tmp_path, FakeStrava(captcha=True), saved_cookie="_strava4_session=good"))
    with pytest.raises(StravaCaptchaError):
        puller.sync(store, date(2026, 9, 1), log=lambda _: None, source=src)
    assert store.settings()["last_sync_strava_web"]["error_code"] == "captcha"


# Gating and scheduling

def test_only_the_owner_with_the_flag_and_a_session_gets_it(tmp_path, monkeypatch):
    monkeypatch.delenv("STRAVA_SESSION_COOKIE", raising=False)
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    accounts = Accounts(tmp_path / "data")
    owner = accounts.create("owner@example.com", "correct horse battery")
    other = accounts.create("friend@example.com", "correct horse battery")
    names = lambda a: [n for n, _ in cli.connected_sources(accounts, a)]  # noqa: E731
    for a in (owner, other):
        accounts.set_credential(a.id, cli.STRAVA_WEB, "cookie", {"cookie": "_strava4_session=good"})
    assert names(owner) == []  # off by default
    accounts.set_strava_web_enabled(other.id, True)
    assert names(other) == []  # never another account
    accounts.set_strava_web_enabled(owner.id, True)
    assert names(owner) == ["strava_web"]
    accounts.set_credential(owner.id, "intervals", "api_key", {"api_key": "k" * 8, "athlete_id": "0"})
    assert names(owner) == ["intervals", "strava_web"]  # after intervals.icu, which it falls back for


def test_a_failing_source_never_stops_the_ones_after_it(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERVALS_API_KEY", "k")
    accounts = Accounts(tmp_path / "data")
    owner = accounts.create("owner@example.com", "correct horse battery")
    accounts.set_strava_web_enabled(owner.id, True)
    accounts.set_credential(owner.id, cli.STRAVA_WEB, "cookie", {"cookie": "_strava4_session=good"})
    store = Store(accounts.user_dir(owner.id))
    order, lines = [], []

    def broken(*a, **k):
        order.append("intervals")
        raise RuntimeError("Garmin data blocked")

    monkeypatch.setattr(cli, "sync", broken)
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print, **k: order.append("strava") or {
        "source": "strava_web", "listed": 2, "new": 1, "saved": 1, "aliased": 1, "duplicates": 0, "error": None,
        "added_ids": [7]})
    out = cli.sync_all(accounts, owner, store, date(2026, 9, 1), log=lines.append)
    assert order == ["intervals", "strava"]
    assert out["saved"] == 1 and out["duplicates"] == 1 and out["added_ids"] == [7]
    assert out["error"] == "intervals.icu: RuntimeError: Garmin data blocked"
    assert [r["label"] for r in out["sources"]] == ["intervals.icu", "Strava"]
    assert "intervals.icu not synced" in lines[0]


def test_cli_sync_runs_every_source_and_exits_non_zero_when_one_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTERVALS_API_KEY", "k")
    monkeypatch.setenv("STRAVA_SESSION_COOKIE", "_strava4_session=good")
    accounts = Accounts(tmp_path / "data")
    accounts.set_strava_web_enabled(accounts.create("owner@example.com", "correct horse battery").id, True)
    order = []

    def broken(*a, **k):
        order.append("intervals")
        raise RuntimeError("Garmin data blocked")

    monkeypatch.setattr(cli, "sync", broken)
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print, **k: order.append("strava") or {"added_ids": []})
    with pytest.raises(SystemExit, match="blocked"):
        cli.main(["sync", "--no-coach"])
    assert order == ["intervals", "strava"]
    order.clear()
    cli.main(["sync", "--no-coach", "--source", "strava_web"])
    assert order == ["strava"]


def test_sync_now_pulls_it_in_the_same_sync(tmp_path, monkeypatch):
    from tests.conftest import sign_in
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INTERVALS_API_KEY", "k")
    monkeypatch.setenv("STRAVA_SESSION_COOKIE", "_strava4_session=good")
    c = sign_in()
    api.get_accounts().set_strava_web_enabled(api.get_accounts().owner().id, True)
    calls = []
    monkeypatch.setattr(cli, "sync", lambda *a, **k: {"source": "intervals", "added_ids": [], "saved": 0})
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print, **k: calls.append(since) or {
        "source": "strava_web", "added_ids": [], "saved": 0})
    r = c.post("/api/sync", json={"days": 7}).json()
    assert len(calls) == 1 and [x["source"] for x in r["sources"]] == ["intervals", "strava_web"]
    assert c.post("/api/sync", json={"days": 7, "source": "intervals"}).status_code == 200
    assert c.post("/api/sync", json={"days": 7, "source": "garmin"}).status_code in (403, 409)
    assert len(calls) == 1  # naming one source pulls just that one


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


# Settings > Data sources: the Strava card's endpoints

def _web(tmp_path, monkeypatch):
    from tests.conftest import sign_in
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("STRAVA_SESSION_COOKIE", raising=False)
    real = puller.open_client
    monkeypatch.setattr(puller, "open_client",
                        lambda folder, *a, **k: real(folder, *a, transport=httpx.MockTransport(FakeStrava()),
                                                     clock=Clock(), sleep=lambda s: None, **k))
    return sign_in()


def test_the_card_saves_a_working_cookie_and_shows_it_without_the_cookie(tmp_path, monkeypatch):
    c = _web(tmp_path, monkeypatch)
    assert c.get("/api/settings").json()["sources"]["strava_web"] == {
        "available": True, "connected": False, "enabled": False, "updated_at": None, "env_override": False,
        "last_sync": None}
    r = c.post("/api/sources/strava-web", json={"cookie": "Cookie: _strava4_session=good; sp=abc"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3 and r.json()["latest"]["name"] == "Afternoon Trail Run"
    assert "good" not in r.text
    shown = c.get("/api/settings").json()["sources"]["strava_web"]
    assert shown["connected"] and shown["enabled"] and shown["updated_at"]
    assert "good" not in c.get("/api/settings").text  # the session never goes back to the browser
    owner = api.get_accounts().owner()
    assert api.get_accounts().credential(owner.id, "strava_web")["cookie"] == "_strava4_session=good; sp=abc"


def test_the_card_saves_nothing_when_strava_refuses_the_cookie(tmp_path, monkeypatch):
    c = _web(tmp_path, monkeypatch)
    r = c.post("/api/sources/strava-web", json={"cookie": "_strava4_session=ended"})
    assert r.status_code == 400 and "session has ended" in r.json()["detail"] and "Nothing was saved" in r.json()["detail"]
    assert c.get("/api/settings").json()["sources"]["strava_web"]["connected"] is False


def test_pull_now_and_turn_off(tmp_path, monkeypatch):
    c = _web(tmp_path, monkeypatch)
    assert c.post("/api/sync", json={"days": 30, "source": "strava_web"}).status_code == 409  # nothing saved yet
    c.post("/api/sources/strava-web", json={"cookie": "_strava4_session=good"})
    calls = []
    monkeypatch.setattr(puller, "sync", lambda store, since, log=print, **k: calls.append(k) or {
        "listed": 3, "new": 1, "saved": 1, "aliased": 2, "duplicates": 0, "error": None, "added_ids": []})
    r = c.post("/api/sync", json={"days": 30, "source": "strava_web"}).json()
    assert calls == [{"saved_cookie": "_strava4_session=good"}]
    assert r["saved"] == 1 and r["duplicates"] == 2  # aliased count as already here
    assert c.delete("/api/sources/strava-web").json() == {"ok": True}
    shown = c.get("/api/settings").json()["sources"]["strava_web"]
    assert not shown["connected"] and not shown["enabled"]


def test_the_card_is_the_owners_only(tmp_path, monkeypatch):
    from tests.conftest import sign_in
    _web(tmp_path, monkeypatch)
    friend = sign_in("friend@example.com", "correct horse battery")
    assert friend.get("/api/settings").json()["sources"]["strava_web"]["available"] is False
    assert friend.post("/api/sources/strava-web", json={"cookie": "_strava4_session=good"}).status_code == 403
    assert friend.delete("/api/sources/strava-web").status_code == 403
    assert friend.post("/api/sync", json={"days": 30, "source": "strava_web"}).status_code == 403
