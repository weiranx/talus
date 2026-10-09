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


def test_load_skips_a_gap_in_synced_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in list(range(200, 190, -1)) + list(range(10, 0, -1)):  # spring, nothing for months, now
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:03d}.gpx"
        f.write_bytes(_gpx(d))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", *files])
    client = TestClient(app)

    data = client.get("/api/load?days=365").json()
    since = (date.today() - timedelta(days=10)).isoformat()
    assert data["history"]["since"] == since
    assert data["history"]["gap"]["to"] == (date.today() - timedelta(days=11)).isoformat()
    series = data["ledgers"]["descent"]["series"]
    assert series[0]["day"] == since and len(series) == 11
    assert data["ledgers"]["descent"]["today"]["building"] is True
    assert not data["ledgers"]["descent"]["today"]["flags"]
    t = client.get("/api/today").json()
    assert t["ledgers"]["climb"]["building"] is True and len(t["ledgers"]["climb"]["spark"]) == 11


def _rows(days):
    return sorted((d.day, d.ledger, round(d.fitness, 6), round(d.fatigue, 6), tuple(d.flags)) for d in days)


def _import_runs(tmp_path, monkeypatch, n=12):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in range(n, 0, -1):
        d = datetime.combine(date.today() - timedelta(days=2 * k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:02d}.gpx"
        f.write_bytes(_gpx(d, n=900))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", "--no-coach", *files])


def test_reads_never_write(tmp_path, monkeypatch):
    """Today, Load and Activity read the stored ledgers and carry them to today in memory."""
    _import_runs(tmp_path, monkeypatch)
    from talus.store import Store

    def no_writes(self):
        raise AssertionError("a read wrote to the database")
    monkeypatch.setattr(Store, "_write", no_writes)
    client = TestClient(app)
    assert client.get("/api/today").status_code == 200
    assert client.get("/api/load").status_code == 200
    aid = client.get("/api/activities").json()[0]["id"]
    assert client.get(f"/api/activities/{aid}").status_code == 200
    assert client.get("/api/explain/ledger/climb/fitness").status_code == 200


def test_refresh_rewrites_only_from_the_changed_day(tmp_path, monkeypatch):
    from talus import ledger
    from talus.config import data_dir
    from talus.store import Store
    _import_runs(tmp_path, monkeypatch, n=25)  # 50 days: past the opening weeks, so the seed is fixed
    store = Store(data_dir())
    assert store.refresh_ledgers() is None  # nothing changed since the import
    before = {(d.day, d.ledger): d.fitness for d in store.ledger_days()}

    newest = store.activities()[0]
    cli.main(["score", str(newest["id"])])  # same score again: marks stale, nothing differs
    assert store.refresh_ledgers() is None

    th = store.thresholds()
    th.ant, th.max = 165, 185
    store.set_thresholds(th)
    row = store.activities()[0]
    from talus.parse import parse
    from talus.score import score
    store.save_score(row["id"], score(parse(store.raw(row)), th))
    first = store.refresh_ledgers()
    assert first == date.fromisoformat(row["start_time"][:10])
    after = {(d.day, d.ledger): d.fitness for d in store.ledger_days()}
    assert all(after[k] == v for k, v in before.items() if k[0] < first)
    built = ledger.build(store.scored_loads())
    assert _rows(store.ledgers()) == _rows(built)


def test_concurrent_requests_dont_lock_the_database(tmp_path, monkeypatch):
    """Pages, check-ins, settings saves and a threshold change (which rescores everything) at
    once: every request succeeds and the stored ledgers end up matching a full rebuild."""
    from concurrent.futures import ThreadPoolExecutor

    from talus import ledger
    from talus.config import data_dir
    from talus.store import Store
    _import_runs(tmp_path, monkeypatch, n=40)  # big enough that the old engine hit "database is locked"
    thresholds = {"resting": 45, "aet": 150, "ant": 170, "max": 190}

    def hit(i):
        client = TestClient(app)
        kind = i % 6
        if kind == 0:
            return client.get("/api/today").status_code
        if kind == 1:
            return client.get("/api/load").status_code
        if kind == 2:
            return client.post("/api/checkin", json={"soreness": 3, "energy": 3, "pain": 1}).status_code
        if kind == 3:
            return client.put("/api/settings/readiness", json={"sleep_need_h": 7.5}).status_code
        if i % 24 == 4:  # a few, since each rescores every activity
            return client.put("/api/settings/thresholds", json=thresholds | {"ant": 168 + i % 4}).status_code
        return client.get("/api/settings").status_code

    with ThreadPoolExecutor(24) as pool:
        codes = list(pool.map(hit, range(96)))
    assert codes == [200] * 96
    store = Store(data_dir())
    assert not store._ledgers_stale()
    built = ledger.build(store.scored_loads())
    assert _rows(store.ledgers()) == _rows(built)
