from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from talus import predict, terrain
from talus.plan import Objective
from talus.route import route_numbers
from tests.conftest import make_streams
from tests.test_plan import OBJ, _setup

DAY = date.today() + timedelta(weeks=10)


def _runs(n=6, climb=12, steep=False):
    """Runs that climb a mile and a half at `climb`% (hiked), run it back down and run flat at each end."""
    out = []
    for k in range(n):
        legs = [(1.0, 0, 3.0 + 0.05 * k), (1.5, climb, 1.3), (1.5, -climb, 3.0), (1.0, 0, 3.0)]
        if steep:
            legs += [(0.5, 20, 0.9), (0.5, -20, 2.2)]
        s = make_streams(legs)
        out.append(terrain.analyze(s, np.diff(s.distance, prepend=0) < 1.5))
    return out


def _route_gpx(climbs=((3.0, 0.0), (2.0, 0.12), (1.0, 0.0), (2.0, -0.12), (1.0, 0.0)), waypoints=()):
    """A planned route (no times) of legs (miles, grade), north along one meridian, with waypoints at miles."""
    pts, lat, ele = [], 37.0, 1500.0
    step_m = 20.0
    for miles, grade in climbs:
        for _ in range(int(miles * 1609.344 / step_m)):
            lat += step_m / 111_195.0
            ele += step_m * grade
            pts.append(f'<trkpt lat="{lat:.7f}" lon="-118.0"><ele>{ele:.1f}</ele></trkpt>')
    wpts = "".join(f'<wpt lat="{37.0 + m * 1609.344 / 111_195.0:.7f}" lon="{lon}"><name>{name}</name></wpt>'
                   for name, m, lon in ((*w, -118.0) if len(w) == 2 else w for w in waypoints))
    return ('<gpx xmlns="http://www.topografix.com/GPX/1/1">' + wpts + "<trk><name>Ridge 15k</name><trkseg>"
            + "".join(pts) + "</trkseg></trk></gpx>").encode()


def test_terrain_keeps_the_flat_share():
    t = _runs(1)[0]
    assert t["version"] == terrain.VERSION == 2
    assert t["flat"]["distance_mi"] > 1.8 and 8 < t["flat"]["pace_min_mi"] < 9.5
    assert terrain.analyze(make_streams([(0.3, 10, 3.0)]), np.zeros(1, bool))["flat"] is None


def test_paces_are_yours_where_you_ran_and_worked_out_elsewhere():
    assert predict.paces(_runs(2)) is None  # too few runs
    p = predict.paces(_runs(6))
    rows = {b["key"]: b for b in p["bands"]}
    assert p["runs"] == 6 and [b["key"] for b in p["bands"]][0] == "flat"
    assert rows["flat"]["source"] == rows["c8"]["source"] == rows["d8"]["source"] == "yours"
    assert rows["c8"]["hike_share"] > 0.9 and abs(rows["c8"]["vert_ft_h"] - 1854) < 100
    # A steeper climb than any run: hiked at the same climbing rate, so slower per mile.
    assert rows["c15"]["source"] == "climb_rate" and rows["c15"]["pace_min_mi"] > rows["c8"]["pace_min_mi"] * 1.5
    rate = 20 / 100 * 5280 / rows["c15"]["pace_min_mi"] * 60
    assert abs(rate - rows["c8"]["vert_ft_h"]) < 1
    # A gentle climb or a descent: the flat pace times the grade's cost.
    assert rows["c3"]["source"] == rows["d15"]["source"] == "flat_cost"
    assert rows["flat"]["pace_min_mi"] < rows["c3"]["pace_min_mi"] < rows["c8"]["pace_min_mi"]
    for b in p["bands"]:
        assert b["fast_min_mi"] < b["pace_min_mi"] < b["slow_min_mi"]
    # Steep runs make that band the athlete's own.
    steep = {b["key"]: b for b in predict.paces(_runs(6, steep=True))["bands"]}
    assert steep["c15"]["source"] == "yours"


def test_route_profile_keeps_waypoints_on_the_route_and_no_coordinates():
    r = route_numbers(_route_gpx(waypoints=(("Saddle AS", 5.0), ("Summit", 5.0), ("Ridge AS", 6.5),
                                            ("Start", 0.02), ("Trailhead parking", 3.0, -117.9))))
    assert r["name"] == "Ridge 15k"
    prof = r["profile"]
    assert set(prof) == {"dist_m", "alt_m", "up_m", "down_m", "stations"}
    assert [s["name"] for s in prof["stations"]] == ["Saddle AS", "Summit", "Ridge AS"]  # the start is left out
    assert abs(prof["stations"][2]["mi"] - 6.5) < 0.05
    assert abs(prof["up_m"][-1] * 3.28084 - r["gain_ft"]) < 2 and abs(prof["down_m"][-1] * 3.28084 - r["loss_ft"]) < 2
    assert 85 < np.diff(prof["dist_m"][:-1]).mean() < 101
    assert predict.valid_profile(prof) == prof
    assert predict.valid_profile({"dist_m": [0, 0], "alt_m": [1, 1], "up_m": [0, 0], "down_m": [0, 0]}) is None
    assert predict.valid_profile({"dist_m": [0, 1]}) is None and predict.valid_profile("x") is None


def test_predict_splits_at_aid_stations_and_adds_up():
    r = route_numbers(_route_gpx(waypoints=(("Saddle AS", 5.0), ("Ridge AS", 7.0))))
    obj = Objective("Ridge 15k", DAY, r["distance_mi"], r["gain_ft"], r["loss_ft"], source="gpx")
    p = predict.paces(_runs(8))
    e = predict.predict(obj, p, r["profile"], longest_h=1.5)
    assert e["from"] == "profile" and e["low_h"] < e["mid_h"] < e["high_h"]
    assert [s["name"] for s in e["splits"]] == ["Saddle AS", "Ridge AS", "Finish"]
    assert abs(sum(s["distance_mi"] for s in e["splits"]) - r["distance_mi"]) < 0.01
    assert abs(sum(s["gain_ft"] for s in e["splits"]) - r["gain_ft"]) < 5
    assert abs(sum(s["minutes"] for s in e["splits"]) - e["mid_h"] * 60) < 0.5
    assert abs(e["splits"][-1]["clock_min"] / 60 - e["mid_h"]) < 0.01
    # The climb is in the first leg: it takes longer a mile than the descent in the second.
    first, second = e["splits"][0], e["splits"][1]
    assert first["minutes"] / first["distance_mi"] > second["minutes"] / second["distance_mi"]
    # About 2 mi up at 20:36, 2 down at 8:55 and 5 flat at 8:50: some 1.7 h.
    assert 1.55 < e["mid_h"] < 1.9
    assert {b["key"] for b in e["bands"]} >= {"flat", "c8", "d8"} and abs(sum(b["share"] for b in e["bands"]) - 1) < 0.01
    assert e["confidence"] == "good" and e["notes"] == []

    # Without stations, splits every mile; a steeper route takes longer.
    plain = route_numbers(_route_gpx())
    e2 = predict.predict(obj, p, plain["profile"])
    assert [s["name"] for s in e2["splits"]][-1] == "Finish" and len(e2["splits"]) == 9
    steep = route_numbers(_route_gpx(((3.0, 0.0), (2.0, 0.22), (1.0, 0.0), (2.0, -0.22), (1.0, 0.0))))
    e3 = predict.predict(Objective("Steep", DAY, steep["distance_mi"], steep["gain_ft"], steep["loss_ft"]), p, steep["profile"])
    assert e3["mid_h"] > e2["mid_h"] * 1.2 and e3["confidence"] != "good"


def test_predict_from_totals_and_slows_on_a_long_day():
    p = predict.paces(_runs(6))
    short = predict.predict(Objective("50k", DAY, 31, 6000, 6000), p, None, longest_h=4.0)
    assert short["from"] == "totals" and short["splits"] == [] and "GPX" in short["notes"][0]
    long = predict.predict(Objective("100k", DAY, 62, 12000, 12000), p, None, longest_h=4.0)
    assert long["mid_h"] > 2 * short["mid_h"]  # the fade: twice the route takes more than twice as long
    assert long["confidence"] == "rough" and any("longest day" in n for n in long["notes"])
    # A profile that no longer matches the objective's distance is set aside for the totals.
    prof = route_numbers(_route_gpx())["profile"]
    e = predict.predict(Objective("50k", DAY, 31, 6000, 6000), p, prof)
    assert e["from"] == "totals" and "doesn't match" in e["notes"][0]
    assert predict.predict(Objective("50k", DAY, 31, 6000, 6000), None, None) is None


def test_plan_page_carries_the_finish_estimate(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    for a in client.get("/api/activities?limit=200").json():  # what the read-ahead does in the background
        assert client.get(f"/api/activities/{a['id']}").status_code == 200
    r = route_numbers(_route_gpx(waypoints=(("Saddle AS", 5.0),)))
    body = OBJ | {k: r[k] for k in ("distance_mi", "gain_ft", "loss_ft")} | {
        "day": DAY.isoformat(), "source": "gpx", "profile": r["profile"]}
    p = client.put("/api/plan/objective", json=body).json()["prediction"]
    assert p["ready"] and p["pending"] == 0 and p["runs"] >= predict.MIN_ANALYSES, p
    assert p["from"] == "profile" and [s["name"] for s in p["splits"]] == ["Saddle AS", "Finish"]
    assert p["low_h"] < p["mid_h"] < p["high_h"]
    # An edit that leaves the profile out keeps it; a hand-typed objective drops it.
    assert client.put("/api/plan/objective", json=body | {"profile": None, "name": "Ridge"}).json()["prediction"]["from"] == "profile"
    manual = client.put("/api/plan/objective", json=body | {"source": "manual", "profile": None}).json()["prediction"]
    assert manual["from"] == "totals" and manual["splits"] == []
    # A bad profile is ignored rather than refused.
    bad = client.put("/api/plan/objective", json=body | {"profile": {"dist_m": [0, 0]}})
    assert bad.status_code == 200 and bad.json()["prediction"]["from"] == "totals"


def test_which_runs_count_and_what_the_coach_reads(tmp_path, monkeypatch):
    assert predict.counts("TrailRun", "race") and predict.counts(None, "race")
    assert not predict.counts("Hike", "race") and predict.counts("Hike", "trip")
    assert not predict.counts("Ride", "trip")

    from talus import api, coach
    client = _setup(tmp_path, monkeypatch)
    for a in client.get("/api/activities?limit=200").json():
        client.get(f"/api/activities/{a['id']}")
    client.put("/api/plan/objective", json=OBJ | {"day": DAY.isoformat()})
    store = api.get_store()  # outside a request: the owner's, who is signed in
    p = api._plan_for_coach(store)
    est = p["finish_estimate"]  # kept with the draft, as it stood when drafted
    assert api._with_finish(store, p)["finish_estimate"] == est  # chat reads it live: nothing new since
    assert est["moving_time_only"] and est["low_h"] <= est["mid_h"] <= est["high_h"]
    assert all(v * 4 == round(v * 4) for v in (est["low_h"], est["mid_h"], est["high_h"]))  # quarter hours
    assert coach._objective(p, date.today())["finish_estimate"] == est
