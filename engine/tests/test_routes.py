from copy import deepcopy
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from talus import api, heatmap, routes
from tests.conftest import sign_in
from tests.test_api import _import_runs


def context(call="go", engine="fresh", legs="fresh", planned=None):
    return {"date": date.today().isoformat(), "call": {"call": call}, "planned": planned,
            "readiness": {"engine": {"state": engine}, "legs": {"state": legs}}}


def activity(i=1, miles=4, gain=200, loss=200, minutes=40, sport="running", **kw):
    return {"id": i, "name": f"Route {i}", "day": (date.today() - timedelta(days=i)).isoformat(),
            "sport": sport, "distance_mi": miles, "gain_ft": gain, "loss_ft": loss,
            "effort_mi": miles + gain / 500, "moving_time_s": minutes * 60,
            "zone_minutes": {"z1": minutes, "z2": 0, "z3": 0}, "notes": [], **kw}


def line(offset=0, reverse=False):
    pts = [(37 + offset, -118), (37.01 + offset, -118), (37.01 + offset, -117.99)]
    return heatmap.encode(pts[::-1] if reverse else pts)


def test_best_three_fit_the_scheduled_distance_and_both_vert_targets():
    acts = [activity(1, miles=8, gain=1200, loss=1200, minutes=90),
            activity(2, miles=4, gain=200, loss=200), activity(3, miles=4.1, gain=210, loss=190),
            activity(4, miles=3.8, gain=190, loss=210), activity(5, miles=4.3, gain=250, loss=250)]
    plan = {"kind": "easy", "miles": 4, "gain_ft": 200, "loss_ft": 200, "minutes": 40}
    before = deepcopy(acts)
    body = routes.recommend(acts, {a["id"]: line(a["id"] / 10) for a in acts}, context(planned=plan))
    assert body["status"] == "ready"
    assert [r["activity_id"] for r in body["routes"]] == [2, 3, 4]
    assert "distance target" in body["routes"][0]["reasons"][0]
    assert acts == before


def test_repeated_tracks_collapse_but_distinct_routes_with_same_name_survive():
    acts = [activity(i, name="Morning run") for i in range(1, 5)]
    body = routes.recommend(acts, {1: line(), 2: line(0.00001), 3: line(reverse=True), 4: line(0.2)}, context())
    assert [r["activity_id"] for r in body["routes"]] == [1, 4]


def test_loaded_legs_filter_descent_and_long_routes_even_when_the_plan_is_big():
    acts = [activity(1, miles=6, minutes=65, loss=400), activity(2, miles=6, minutes=65, loss=500),
            activity(3, miles=6, minutes=65, loss=1800), activity(4, miles=10, minutes=100, loss=100)]
    plan = {"kind": "long", "miles": 15, "gain_ft": 4000, "loss_ft": 4000, "minutes": 180}
    body = routes.recommend(acts, {a["id"]: line(a["id"]) for a in acts}, context("modify", legs="loaded", planned=plan))
    assert [r["activity_id"] for r in body["routes"]] == [1]
    assert "500 ft" in body["routes"][0]["reasons"][0]


def test_both_loaded_require_a_short_flat_route():
    acts = [activity(1), activity(2, gain=1000), activity(3, loss=900), activity(4, miles=6.5, minutes=65)]
    body = routes.recommend(acts, {a["id"]: line(a["id"]) for a in acts}, context("modify", "loaded", "loaded"))
    assert [r["activity_id"] for r in body["routes"]] == [1]


def test_routes_below_the_stated_time_window_are_not_labeled_as_matches():
    body = routes.recommend([activity(minutes=20)], {1: line()}, context())
    assert body["status"] == "empty" and not body["routes"]


def test_coach_can_only_ease_the_route_targets():
    acts = [activity(1), activity(2, miles=10, gain=2000, loss=2000, minutes=120)]
    plan = {"kind": "long", "miles": 10, "gain_ft": 2000, "loss_ft": 2000, "minutes": 120}
    lines = {1: line(), 2: line(1)}
    body = routes.recommend(acts, lines, context(planned=plan), "modify")
    assert body["target"]["call"] == "modify"
    assert [r["activity_id"] for r in body["routes"]] == [1]
    assert routes.recommend(acts, lines, context("rest"), "go")["routes"] == []
    assert routes.recommend(acts, lines, context(), "rest")["status"] == "rest"


@pytest.mark.parametrize("plan", [None, {"kind": "rest"}])
def test_rest_call_or_scheduled_rest_never_suggest_training(plan):
    body = routes.recommend([activity()], {1: line()}, context("rest" if plan is None else "go", planned=plan))
    assert body["status"] == "rest" and body["routes"] == []


@pytest.mark.parametrize("changes", [
    {"sport": "cycling"}, {"sport": "ski_touring"}, {"sport": "treadmill_running"},
    {"notes": ["No altitude data: vert and vert loads are zero"]},
    {"distance_mi": float("nan")}, {"day": (date.today() + timedelta(days=1)).isoformat()},
])
def test_unsuitable_or_unknown_terrain_is_not_a_recovery_route(changes):
    a = activity() | changes
    assert routes.recommend([a], {1: line()}, context("modify", legs="loaded"))["routes"] == []


@pytest.mark.parametrize("track", [None, "!", heatmap.encode([(0, 0)]), heatmap.encode([(999, 999), (1000, 1000)])])
def test_no_track_or_corrupt_track_yields_an_honest_empty_state(track):
    body = routes.recommend([activity()], {1: track}, context())
    assert body["status"] == "empty" and body["routes"] == []


def test_easy_pace_prevents_a_raced_route_from_looking_short_enough():
    acts = [activity(i, miles=4, gain=0, loss=0, minutes=40) for i in range(1, 4)]
    acts.append(activity(4, miles=6, gain=0, loss=0, minutes=35, zone_minutes={"z1": 0, "z2": 35, "z3": 0}))
    body = routes.recommend(acts, {a["id"]: line(a["id"]) for a in acts}, context("modify", "loaded", "loaded"))
    assert 4 not in [r["activity_id"] for r in body["routes"]]
    assert all(r["time_basis"] == "easy_pace" and r["minutes"] == 40 for r in body["routes"])


def test_bad_history_does_not_poison_the_easy_pace_estimate():
    acts = [activity(i) for i in range(1, 4)]
    acts.append(activity(4, effort_mi=float("inf")))
    acts.append(activity(5, moving_time_s=float("nan")))
    body = routes.recommend(acts, {a["id"]: line(a["id"]) for a in acts}, context())
    assert len(body["routes"]) == 3
    assert all(r["minutes"] == 40 for r in body["routes"])


def test_time_only_session_does_not_demand_zero_distance():
    plan = {"kind": "easy", "miles": 0, "gain_ft": 200, "loss_ft": 200, "minutes": 40}
    body = routes.recommend([activity()], {1: line()}, context(planned=plan))
    assert len(body["routes"]) == 1


def test_completed_run_suppresses_unscheduled_extra_training():
    body = routes.recommend([activity(day=date.today().isoformat()), activity(2)], {2: line()}, context())
    assert body["status"] == "done" and not body["routes"]


def test_api_route_matches_use_the_signed_in_accounts_tracks(tmp_path, monkeypatch):
    _import_runs(tmp_path, monkeypatch, n=3)
    monkeypatch.setattr(api, "warm_details", lambda *a, **k: None)
    owner = sign_in()
    assert TestClient(api.app).get("/api/routes/today").status_code == 401
    acts = owner.get("/api/activities").json()
    a = acts[0]
    # Prime the detail cache; route matching must reuse it instead of reparsing raw files.
    for item in acts:
        assert owner.get(f"/api/activities/{item['id']}").status_code == 200
    monkeypatch.setattr(api, "_streams", lambda *a: (_ for _ in ()).throw(AssertionError("read again")))
    assert owner.post("/api/schedule", json={"day": date.today().isoformat(), "kind": "easy",
                      "miles": a["distance_mi"], "gain_ft": a["gain_ft"], "loss_ft": a["loss_ft"],
                      "minutes": a["moving_time_s"] / 60}).status_code == 200
    body = owner.get("/api/routes/today").json()
    assert body["pending"] == 0 and len(body["routes"]) == 1
    assert body["routes"][0]["activity_id"] == a["id"]
    assert owner.get("/api/routes/today?call=rest").json()["routes"] == []
    assert owner.get("/api/routes/today?call=hard").status_code == 422
    friend = sign_in("routes-friend@example.com", "another long password")
    assert friend.get("/api/routes/today").status_code == 409
    from talus.models import Thresholds
    from talus.store import Store
    accounts = api.get_accounts()
    store = Store(accounts.user_dir(accounts.by_email("routes-friend@example.com").id))
    store.set_thresholds(Thresholds(45, 150, 172, 190))
    store.db.close()
    body = friend.get("/api/routes/today").json()
    assert body["status"] == "empty" and not body["routes"] and body["pending"] == 0
