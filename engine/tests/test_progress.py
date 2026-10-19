from copy import deepcopy
from datetime import date
import pytest
from talus.heatmap import encode
from talus.progress import build, route_points, same_route

TODAY = date(2026, 10, 19)
LINE = encode([(37, -122), (37.02, -122), (37.04, -122.01)])

def record(i, day, pace=10):
    return {"id": i, "day": day, "name": "Loop", "sport": "running", "line": LINE,
            "summary": {"distance_mi": 8, "moving_time_s": pace * 480, "hr_hist": {"140": pace * 480}},
            "terrain": {"bands": [{"key": "c8", "label": "8 to 15%", "kind": "climb", "time_s": 900,
                        "vert_rate_ft_h": 1800, "avg_hr": 140, "hike_share": .2}],
                        "durability": {"pace_change_pct": 5}}}

def history():
    return [record(4, "2026-10-19", 9), record(3, "2026-10-05"), record(2, "2026-09-20"), record(1, "2026-09-01")]

def signals(rows):
    return {s["metric"]: s for s in build(rows, TODAY)["signals"]}

def test_median_changes_evidence_and_real_zero():
    rows = history()
    rows[0]["terrain"]["bands"][0]["vert_rate_ft_h"] = 1980
    rows[0]["terrain"]["durability"]["pace_change_pct"] = 0
    result = signals(rows)
    assert result["route"]["change"] == -10
    assert result["climb"]["change"] == 10
    assert result["durability"]["change"] == -5
    assert result["route"]["baseline"] == 10
    assert [r["id"] for r in result["route"]["evidence"]] == [4, 3, 2, 1]

@pytest.mark.parametrize("mutation", ["hr", "coverage", "future", "stale", "duplicate", "sport", "distance", "gps"])
def test_incomparable_routes_stay_unavailable(mutation):
    rows = history()
    if mutation == "hr": rows[0]["summary"]["hr_hist"] = {"150": 5000}
    if mutation == "coverage": rows[0]["summary"]["hr_hist"] = {"140": 500}
    if mutation == "future": rows[0]["day"] = "2026-10-20"
    if mutation == "stale":
        for r in rows: r["day"] = "2026-08-01"
    if mutation == "duplicate": rows[1]["day"] = rows[2]["day"]
    if mutation == "sport": rows[1]["sport"] = "cycling"
    if mutation == "distance": rows[1]["summary"]["distance_mi"] = 9
    if mutation == "gps": rows[1]["line"] = None
    assert signals(rows)["route"]["latest"] is None

def test_climbs_require_grade_gait_time_hr_and_duration():
    for field, value in [("key", "c15"), ("hike_share", .8), ("time_s", 599), ("avg_hr", None)]:
        rows = history()
        rows[1]["terrain"]["bands"][0][field] = value
        assert signals(rows)["climb"]["latest"] is None
    rows = history()
    rows[1]["summary"].update(moving_time_s=10000, hr_hist={"140": 10000})
    assert signals(rows)["climb"]["latest"] is None

def test_missing_terrain_and_nan_are_not_zero():
    rows = history()
    for r in rows: r["terrain"] = None
    assert signals(rows)["durability"]["latest"] is None
    assert signals(rows)["climb"]["latest"] is None
    assert signals(rows)["route"]["latest"] == 9
    for r in rows: r["summary"]["hr_hist"] = {"140": float("nan")}
    assert all(s["latest"] is None for s in signals(rows).values())

def test_gps_order_and_location():
    a = route_points(LINE)
    assert same_route(a, a)
    assert not same_route(a, a[::-1])
    assert not same_route(a, a + [.005, 0])
    assert route_points("broken") is None

def test_no_mutation_and_history_bounds():
    rows = history()
    before = deepcopy(rows)
    build(rows, TODAY)
    assert rows == before
    rows.append(record(5, "2025-01-01"))
    rows.append(record(6, "2026-10-18"))
    rows[-1]["sport"] = "ski_touring"
    assert build(rows, TODAY)["activities"] == 4

def test_empty():
    assert all(s["latest"] is None for s in signals([]).values())

def test_api_requires_login_and_keeps_accounts_separate(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from fastapi.testclient import TestClient
    from talus import api, cli
    from tests.conftest import sign_in
    from tests.test_api import _gpx

    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(api, "warm_details", lambda *a, **k: None)
    assert TestClient(api.app).get("/api/progress").status_code == 401
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    for i in range(4):
        file = tmp_path / f"run{i}.gpx"
        file.write_bytes(_gpx(datetime.combine(date.today() - timedelta(days=i * 7), datetime.min.time()), n=500))
        cli.main(["import", str(file), "--no-coach"])
    from talus.store import Store
    store = Store(tmp_path / "data" / "users" / "1")
    store.db.execute("UPDATE activity SET sport = ?", ("running",))
    store.db.commit()
    store.db.close()
    client = sign_in()
    response = client.get("/api/progress")
    assert response.status_code == 200, response.text
    assert response.json()["activities"] == 4
    assert len(response.json()["signals"]) == 3
    other = sign_in("second@example.com")
    response = other.get("/api/progress")
    assert response.status_code == 200
    assert response.json()["activities"] == 0
    assert all(s["latest"] is None for s in response.json()["signals"])
