"""COROS direct: the Training Hub API, owner only, no password kept."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

import httpx
import pytest

from talus import cli
from talus.accounts import Accounts
from talus.config import data_dir
from talus.sources import coros as coros_mod
from talus.sources.coros import CorosSignedOut, CorosSource

from .conftest import sign_in
from .test_parse import _gpx

ok = lambda data: httpx.Response(200, json={"result": "0000", "message": "OK", "data": data})  # noqa: E731

RUN = {"labelId": "476", "name": "Mt Wilson", "sportType": 102, "device": "COROS VERTIX 2",
       "startTime": 1790866800, "startTimezone": -28, "distance": 22530.0, "ascent": 1450}  # 2026-10-01 15:00Z, UTC-7


def _api(requests):
    def handle(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        path = req.url.path
        if path == "/activity/query":
            page = int(req.url.params["pageNumber"])
            hike = RUN | {"labelId": "477", "sportType": 104, "name": "", "startTime": RUN["startTime"] + 86400}
            return ok({"count": 2, "totalPage": 2, "pageNumber": page, "dataList": [RUN] if page == 1 else [hike]})
        if path == "/activity/detail/download":
            return ok({"fileUrl": "https://oss.coros.com/fit/476.fit"})
        if path == "/analyse/query":
            return ok({"dayList": [{"happenDay": 20261001, "avgSleepHrv": 61, "rhr": 47},
                                   {"happenDay": 20261002, "rhr": 0}]})
        raise AssertionError(path)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_coros_source_lists_runs_in_local_time_and_reads_files_and_nights():
    seen, files_seen = [], []
    files = httpx.Client(transport=httpx.MockTransport(
        lambda req: files_seen.append(req) or httpx.Response(200, content=_gpx())))
    src = CorosSource.from_credential({"kind": "tokens", "tokens": "tok", "user_id": "9", "region": "en"},
                                      client=_api(seen), files=files)
    acts = src.list_activities(date(2026, 9, 1), date(2026, 10, 13))
    assert [a.source_id for a in acts] == ["476", "477"]
    run, hike = acts
    assert run.start_time == datetime(2026, 10, 1, 8, 0)  # wall clock where it was run
    assert (run.sport, hike.sport, hike.name) == ("trail_running", "hiking", None)
    assert run.distance_m == 22530.0 and run.gain_m == 1450 and run.device_name == "COROS VERTIX 2"
    q = seen[0]
    assert q.url.host == "teamapi.coros.com" and q.headers["accesstoken"] == "tok"
    assert json.loads(q.headers["yfheader"]) == {"userId": "9"}
    assert q.url.params["startDay"] == "20260901" and q.url.params["endDay"] == "20261013"

    raw = src.fetch(run)
    assert raw.fmt == "gpx"
    dl = seen[-1]
    assert dl.url.params["fileType"] == "4" and dl.url.params["sportType"] == "102"
    assert "accesstoken" not in files_seen[0].headers  # the token stays with COROS

    nights = src.list_wellness(date(2026, 10, 1), date(2026, 10, 2))
    assert [(n.day, n.hrv, n.resting_hr, n.sleep_s) for n in nights] == [(date(2026, 10, 1), 61, 47, None)]


def test_coros_sign_out_and_errors_are_plain():
    signed_out = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"result": "1019", "message": "Access token is invalid"})))
    src = CorosSource("tok", client=signed_out)
    with pytest.raises(CorosSignedOut, match="Sign in to COROS again"):
        src.list_activities(date(2026, 10, 1))
    limited = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429, text="slow down")))
    with pytest.raises(coros_mod.CorosError, match="rate-limiting"):
        CorosSource("tok", client=limited).list_activities(date(2026, 10, 1))


def test_coros_login_sends_a_hash_and_keeps_only_the_token():
    sent = []

    def handle(req):
        sent.append(req)
        body = json.loads(req.content)
        if body["pwd"] != hashlib.md5(b"right").hexdigest():
            return httpx.Response(200, json={"result": "1030", "message": "Wrong password"})
        return ok({"accessToken": "tok", "userId": 9})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    assert coros_mod.login("me@example.com", "right", "eu", client=client) == \
        {"tokens": "tok", "user_id": "9", "region": "eu"}
    assert sent[0].url.host == "teameuapi.coros.com"
    with pytest.raises(coros_mod.CorosError, match="Wrong password"):
        coros_mod.login("me@example.com", "wrong", client=client)


def test_coros_sync_skips_runs_another_source_has(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    cli.main(["thresholds", "--resting", "45", "--ant", "172", "--max", "190"])
    from talus.store import Store
    store = Store(cli.default_user_dir(Accounts(data_dir())))
    seen = []
    files = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=_gpx())))
    src = CorosSource("tok", client=_api(seen), files=files)
    r = cli.sync(store, "coros", date(2026, 9, 1), log=lambda *_: None, source=src)
    assert r["listed"] == 2 and r["saved"] == 2 and r["wellness_days"] >= 0
    assert store.settings()["last_sync_coros"]["saved"] == 2
    again = cli.sync(store, "coros", date(2026, 9, 1), log=lambda *_: None, source=src)
    assert again["new"] == 0


def test_coros_connect_is_owner_only_and_keeps_no_password(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    owner = sign_in()
    calls = []

    def login(email, password, region="en", client=None):
        calls.append((email, region))
        return {"tokens": "tok", "user_id": "9", "region": region}
    monkeypatch.setattr(coros_mod, "login", login)
    r = owner.post("/api/sources/coros", json={"email": "me@example.com", "password": "hunter2hunter2", "region": "eu"})
    assert r.json() == {"status": "connected"} and calls == [("me@example.com", "eu")]

    cred = Accounts(data_dir()).credential(1, "coros")
    assert cred["tokens"] == "tok" and cred["region"] == "eu" and "password" not in cred
    assert b"hunter2" not in (data_dir() / "accounts.db").read_bytes()
    c = owner.get("/api/settings").json()["sources"]["coros"]
    assert c["available"] and c["connected"] and c["region"] == "eu"

    other = sign_in("friend@example.com", "another long password")
    assert other.post("/api/sources/coros", json={"email": "x@y.z", "password": "p"}).status_code == 403
    assert other.post("/api/sync", json={"source": "coros"}).status_code == 403
    assert not other.get("/api/settings").json()["sources"]["coros"]["available"]

    assert owner.delete("/api/sources/coros").json() == {"ok": True}
    assert Accounts(data_dir()).credential(1, "coros") is None
