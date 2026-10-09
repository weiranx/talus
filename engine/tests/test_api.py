from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from talus import cli
from talus.api import app


def _gpx(start: datetime, n=1800, climb_per_s=0.4, hr=140):
    pts = []
    for i in range(n):
        half = n // 2
        ele = 1500 + (i if i < half else n - i) * climb_per_s
        t = start + timedelta(seconds=i)
        pts.append(f'<trkpt lat="{37 + i * 0.00003:.6f}" lon="-118.0"><ele>{ele:.1f}</ele>'
                   f'<time>{t:%Y-%m-%dT%H:%M:%SZ}</time><extensions><gpxtpx:TrackPointExtension>'
                   f'<gpxtpx:hr>{hr}</gpxtpx:hr><gpxtpx:cad>84</gpxtpx:cad></gpxtpx:TrackPointExtension>'
                   f'</extensions></trkpt>')
    return ('<gpx xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
            + "".join(pts) + "</trkseg></trk></gpx>").encode()


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in range(40, 0, -1):
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:02d}.gpx"
        f.write_bytes(_gpx(d, climb_per_s=1.0 if k == 1 else 0.3))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", *files])
    client = TestClient(app)

    t = client.get("/api/today").json()
    assert set(t["ledgers"]) == {"cardio", "climb", "descent"}
    assert t["readiness"]["legs"]["state"] in ("loaded", "overreached")
    assert t["call"]["source"] == "rules"
    assert len(t["ledgers"]["descent"]["spark"]) == 41  # history starts 40 days ago

    assert client.post("/api/checkin", json={"soreness": 5, "energy": 3, "pain": 1}).json() == {"ok": True}
    t = client.get("/api/today").json()
    assert t["checkin"]["soreness"] == 5 and t["readiness"]["legs"]["state"] == "overreached"
    assert client.post("/api/checkin", json={"soreness": 9, "energy": 3, "pain": 1}).status_code == 422

    load = client.get("/api/load?days=42").json()
    assert len(load["ledgers"]["climb"]["series"]) == 41
    assert len(load["intensity_by_week"]) == 8
    assert load["biggest_days"][0]["gain_ft"] > 2500
    assert abs(load["thresholds"]["aerobic_gap"] - 22 / 172) < 1e-9

    acts = client.get("/api/activities").json()
    big = acts[0]
    detail = client.get(f"/api/activities/{big['id']}").json()
    assert abs(detail["gain_ft"] - big["gain_ft"]) < 1
    assert detail["loads"]["descent"]["six_week_high"] is True
    assert detail["loads"]["descent"]["times_usual_day"] > 2
    assert [s["kind"] for s in detail["segments"]] == ["climb", "descent"]
    assert abs(sum(s["climb_load"] for s in detail["segments"]) - big["climb_load"]) < 1
    assert abs(sum(s["descent_load"] for s in detail["segments"]) - big["descent_load"]) < 1
    assert len(detail["streams"]["altitude_ft"]) <= 1000
    assert set(detail["streams"]["zone"]) == {1}
    assert client.get("/api/activities/999").status_code == 404
