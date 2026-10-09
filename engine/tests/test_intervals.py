import gzip
import json
from datetime import date

import httpx

from talus.sources.intervals import IntervalsSource

ACTIVITIES = [
    {"id": "i101", "start_date_local": "2026-10-01T07:00:00", "name": "Baldy", "type": "Run",
     "distance": 20921.0, "total_elevation_gain": 1371.6, "source": "GARMIN_CONNECT",
     "device_name": "Garmin Fenix 8"},
    {"id": "i102", "start_date_local": "2026-10-02T07:00:00", "name": "Easy", "type": "Run",
     "distance": 8000.0, "total_elevation_gain": 50.0, "source": "OAUTH_CLIENT"},
    {"id": "9999", "start_date_local": "2026-10-03T07:00:00", "source": "STRAVA"},
]
FIT_HEADER = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FIT\x00\x00"


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"].startswith("Basic ")
    path = request.url.path
    if path == "/api/v1/athlete/i42/activities":
        assert request.url.params["oldest"] == "2026-09-01"
        return httpx.Response(200, json=ACTIVITIES)
    if path == "/api/v1/activity/i101/file":
        return httpx.Response(200, content=gzip.compress(FIT_HEADER))
    if path == "/api/v1/activity/i102/file":
        return httpx.Response(404)
    if path == "/api/v1/activity/i102/streams.json":
        return httpx.Response(200, json=[{"type": "time", "data": [0, 1]}])
    return httpx.Response(404)


def source():
    client = httpx.Client(base_url="https://intervals.icu/api/v1", transport=httpx.MockTransport(handler))
    return IntervalsSource(api_key="k", athlete_id="i42", client=client)


def test_list_skips_strava():
    acts = source().list_activities(date(2026, 9, 1), date(2026, 10, 9))
    assert [a.source_id for a in acts] == ["i101", "i102"]
    assert acts[0].device_name == "Garmin Fenix 8"
    assert acts[0].gain_m == 1371.6


def test_fetch_original_fit_then_streams_fallback():
    src = source()
    a, b = src.list_activities(date(2026, 9, 1), date(2026, 10, 9))
    raw = src.fetch(a)
    assert raw.fmt == "fit" and raw.data == FIT_HEADER
    raw = src.fetch(b)
    assert raw.fmt == "icu-streams" and json.loads(raw.data)[0]["type"] == "time"
