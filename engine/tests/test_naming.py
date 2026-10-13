from talus import api, coach, naming
from tests.test_coach import FakeClient, _demo


def test_generic_names_are_the_ones_a_device_makes_up():
    for name in ("Pasadena Trail Running", "Morning Run", "Running", "Hiking", "Evening Trail Run", "run01.gpx",
                 "Los Angeles County Hiking", "", None, "Treadmill Running", "run01", "2026-10-11-071500"):
        assert naming.is_generic(name), name
    for name in ("Baldy via the Bowl", "Sunset loop with Sam", "Recovery jog, tired legs", "Mt Wilson TT"):
        assert not naming.is_generic(name), name


def test_route_shape_from_the_track():
    def detail(pts, alt=None):
        return {"streams": {"lat": [p[0] for p in pts], "lon": [p[1] for p in pts],
                            "altitude_ft": alt or [0] * len(pts)}}
    out = [(34.0 + k * 0.001, -118.0) for k in range(30)]
    assert naming.features(detail(out + out[::-1]))[0]["shape"] == "out and back"
    assert naming.features(detail(out))[0]["shape"] == "point to point"
    loop = [(34.0 + 0.01 * __import__("math").sin(t / 5), -118.0 + 0.01 * (1 - __import__("math").cos(t / 5)))
            for t in range(32)]
    assert naming.features(detail(loop))[0]["shape"] == "loop"
    alt = list(range(30)) + list(range(30, 0, -1))
    assert naming.features(detail(out + out[::-1], alt))[2] == out[-1]


def test_activities_get_coach_names_that_never_beat_a_typed_name(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(naming, "area_name", lambda lat, lon: asked.append("area") or "Griffith Park")
    monkeypatch.setattr(naming, "peak_name", lambda lat, lon: asked.append("peak") or "Mount Hollywood")
    acts = client.get("/api/activities").json()
    aid = acts[0]["id"]
    assert acts[0]["name_by"] == "source"
    fake = FakeClient({"name": "Mount Hollywood loop"})
    monkeypatch.setattr(api, "coach_client", lambda: fake)
    r = client.post(f"/api/activities/{aid}/name/suggest").json()
    assert r["name"] == "Mount Hollywood loop" and r["source"] == "coach"
    call = fake.calls[0]
    assert call["model"] == coach.BRIEF_MODEL and "Mode: activity name." in call["messages"][0]["content"]
    assert '"peak_near_high_point": "Mount Hollywood"' in call["messages"][0]["content"]
    assert '"lat"' not in call["messages"][0]["content"]  # place names only, never coordinates
    a = client.get(f"/api/activities/{aid}").json()
    assert (a["name"], a["name_by"]) == ("Mount Hollywood loop", "ai") and a["source_name"] == "run01"
    # A typed name wins, and clearing it goes back to the coach's.
    assert client.put(f"/api/activities/{aid}/name", json={"name": "Sunrise with Sam"}).json()["name_by"] == "you"
    assert client.get("/api/activities").json()[0]["name"] == "Sunrise with Sam"
    assert client.put(f"/api/activities/{aid}/name", json={"name": ""}).json()["name"] == "Mount Hollywood loop"
    # Turning AI names off shows the device's names again.
    client.put("/api/settings/names", json={"enabled": False})
    assert client.get("/api/activities").json()[0]["name_by"] == "source"
    # Places are cached by position: a second suggestion asks OpenStreetMap nothing new.
    n = len(asked)
    fake.replies.append({"name": "Mount Hollywood again"})
    client.post(f"/api/activities/{aid}/name/suggest")
    assert len(asked) == n


def test_backfill_names_every_generic_activity_once(tmp_path, monkeypatch):
    client = _demo(tmp_path, monkeypatch)
    monkeypatch.setattr(naming, "area_name", lambda lat, lon: "Griffith Park")
    monkeypatch.setattr(naming, "peak_name", lambda lat, lon: None)
    monkeypatch.setattr(api, "coach_client", lambda: None)  # rules name from the place
    s = client.get("/api/names/backfill").json()
    assert s["pending"] == 30 and s["estimate_usd"] >= 0 and not s["running"]
    client.post("/api/names/backfill")
    import time
    for _ in range(200):
        s = client.get("/api/names/backfill").json()
        if not s["running"]:
            break
        time.sleep(0.1)
    assert s["pending"] == 0 and s["named"] == 30
    assert {a["name"] for a in client.get("/api/activities").json()} == {"Run at Griffith Park"}
