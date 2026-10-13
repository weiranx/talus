import time

import numpy as np

from talus import api, heatmap
from tests.conftest import sign_in
from tests.test_api import _import_runs


def test_simplify_keeps_corners_and_drops_straight_points():
    lat = np.concatenate([np.linspace(37, 37.01, 200), np.full(200, 37.01)])
    lon = np.concatenate([np.full(200, -118.0), np.linspace(-118, -117.99, 200)])
    pts = heatmap.simplify(lat, lon)
    assert len(pts) == 3
    assert pts[1] == (37.01, -118.0)


def test_simplify_skips_missing_and_null_island_points():
    lat = np.array([np.nan, 0.0, 37.0, 37.001, 37.002])
    lon = np.array([np.nan, 0.0, -118.0, -118.0, -118.0])
    assert heatmap.simplify(lat, lon) == [(37.0, -118.0), (37.002, -118.0)]


def test_polyline_round_trip():
    pts = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert heatmap.encode(pts) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"  # Google's documented example
    assert heatmap.decode(heatmap.encode(pts)) == pts


def test_kinds():
    assert heatmap.kind("TrailRun") == "trail"
    assert heatmap.kind("trail_running") == "trail"
    assert heatmap.kind("running") == "run"
    assert heatmap.kind("Hike") == "hike"
    assert heatmap.kind("MountainBikeRide") == "ride"
    assert heatmap.kind("swimming") == "other"
    assert heatmap.kind(None) == "other"


def _all_lines(client, wait_s=20.0):
    deadline = time.monotonic() + wait_s
    while True:
        body = client.get("/api/heatmap").json()
        if body["pending"] == 0 or time.monotonic() > deadline:
            return body
        time.sleep(0.2)


def test_heatmap_reads_each_file_once(tmp_path, monkeypatch):
    _import_runs(tmp_path, monkeypatch, n=3)
    monkeypatch.setattr(api, "warm_details", lambda *a, **k: None)
    client = sign_in()
    body = _all_lines(client)
    assert body["pending"] == 0 and len(body["activities"]) == 3
    a = body["activities"][0]
    assert a["kind"] == heatmap.kind(a["sport"]) and a["distance_mi"] > 0 and a["gain_ft"] > 0
    pts = heatmap.decode(a["line"])
    assert len(pts) >= 2 and abs(pts[0][0] - 37) < 0.001 and pts[0][1] == -118.0

    # Cached: no file is read again.
    monkeypatch.setattr(api, "_streams", lambda *a: (_ for _ in ()).throw(AssertionError("read again")))
    assert client.get("/api/heatmap").json() == body


def test_heatmap_uses_the_activity_page_cache(tmp_path, monkeypatch):
    _import_runs(tmp_path, monkeypatch, n=2)
    monkeypatch.setattr(api, "warm_details", lambda *a, **k: None)
    client = sign_in()
    for a in client.get("/api/activities").json():
        client.get(f"/api/activities/{a['id']}")
    monkeypatch.setattr(api, "_streams", lambda *a: (_ for _ in ()).throw(AssertionError("read again")))
    body = client.get("/api/heatmap").json()
    assert body["pending"] == 0 and all(a["line"] for a in body["activities"])
