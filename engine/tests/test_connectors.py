"""More than one data source: Garmin direct, file upload, Apple Health, and de-duplication."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta

import pytest

from talus import cli
from talus.accounts import Accounts
from talus.config import data_dir
from talus.sources import garmin as garmin_mod
from talus.sources.apple_health import read_export
from talus.sources.base import RemoteActivity, WellnessDay
from talus.sources.garmin import GarminSource
from talus.store import Store

from .conftest import sign_in
from .test_parse import _gpx

DAY = datetime(2026, 10, 1, 15, 0)  # the _gpx() run starts 2026-10-01 15:00Z


class FakeSource:
    def __init__(self, name, acts, tokens=None):
        self.name, self.acts, self.fetched, self._tokens = name, acts, [], tokens

    def list_activities(self, since, until=None):
        return self.acts

    def fetch(self, act):
        from talus.sources.base import RawFile
        self.fetched.append(act.source_id)
        return RawFile(_gpx(), "gpx")


def _store(tmp_path, monkeypatch) -> Store:
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    cli.main(["thresholds", "--resting", "45", "--ant", "172", "--max", "190"])
    return Store(cli.default_user_dir(Accounts(data_dir())))


def test_same_run_from_two_sources_is_kept_once(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    run = RemoteActivity("intervals", "i1", DAY, "Run", "Run", distance_m=2000)
    cli.sync(store, "intervals", date(2026, 9, 1), log=lambda *_: None, source=FakeSource("intervals", [run]))
    assert len(store.activities()) == 1

    # Garmin lists the same run a minute off with nearly the same distance, and a different run.
    same = RemoteActivity("garmin", "g1", DAY + timedelta(seconds=60), "Run", "running", distance_m=2050)
    other = RemoteActivity("garmin", "g2", DAY + timedelta(days=1), "Hike", "hiking", distance_m=8000)
    src = FakeSource("garmin", [same, other])
    r = cli.sync(store, "garmin", date(2026, 9, 1), log=lambda *_: None, source=src)
    assert (r["duplicates"], r["new"], r["saved"]) == (1, 1, 1)
    assert src.fetched == ["g2"]  # the duplicate is never downloaded
    assert store.settings()["last_sync_garmin"]["duplicates"] == 1

    # Same start but a clearly different distance is a different activity (a double session).
    assert store.duplicate_of(DAY, 9000) is None
    assert store.duplicate_of(DAY + timedelta(minutes=2)) is not None
    assert store.duplicate_of(DAY + timedelta(minutes=10)) is None


def test_garmin_source_reads_activities_files_and_nights():
    class Api:
        class ActivityDownloadFormat:
            ORIGINAL = "original"

        class client:
            @staticmethod
            def dumps():
                return '{"di_token": "new"}'

        def get_activities_by_date(self, start, end):
            return [{"activityId": 7, "startTimeLocal": "2026-10-01 08:00:00", "activityName": "Ridge",
                     "activityType": {"typeKey": "trail_running"}, "distance": 16093.4, "elevationGain": 914.4}]

        def download_activity(self, activity_id, dl_fmt=None):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("7_ACTIVITY.gpx", _gpx())
            return buf.getvalue()

        def get_sleep_data(self, day):
            if day == "2026-10-02":
                return {}
            return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 81}}},
                    "avgOvernightHrv": 62.0, "restingHeartRate": 48}

    src = GarminSource(tokens='{"di_token": "old"}', api=Api())
    [a] = src.list_activities(date(2026, 9, 1))
    assert (a.source_id, a.sport, a.start_time) == ("7", "trail_running", datetime(2026, 10, 1, 8))
    assert src.fetch(a).fmt == "gpx"
    nights = src.list_wellness(date(2026, 10, 1), date(2026, 10, 3))
    assert [n.day.day for n in nights] == [1, 3]
    assert (nights[0].hrv, nights[0].resting_hr, nights[0].sleep_s, nights[0].sleep_score) == (62.0, 48, 27000, 81)
    assert src.tokens() == '{"di_token": "new"}'


def test_garmin_sync_saves_refreshed_tokens_and_asks_only_for_missing_nights(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.save_wellness([WellnessDay("garmin", date.today() - timedelta(days=1), hrv=60)])
    asked = []

    class Src(FakeSource):
        wellness_per_day = True

        def tokens(self):
            return "refreshed"

        def list_wellness(self, since, until=None):
            asked.append(since)
            return []

    saved = []
    cli.sync(store, "garmin", date.today() - timedelta(days=30), log=lambda *_: None,
             credential={"kind": "tokens", "tokens": "old"}, save_credential=saved.append, source=Src("garmin", []))
    assert saved == [{"tokens": "refreshed"}]
    assert asked == [date.today() - timedelta(days=3)]


def test_garmin_connect_is_owner_only_and_keeps_no_password(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    owner = sign_in()
    calls = []

    def start(account_id, email, password):
        calls.append((email, password))
        return None  # Garmin asks for an MFA code

    monkeypatch.setattr(garmin_mod, "start_login", start)
    monkeypatch.setattr(garmin_mod, "finish_login", lambda account_id, code: '{"di_token": "t"}')
    r = owner.post("/api/sources/garmin", json={"email": "me@example.com", "password": "hunter2hunter2"})
    assert r.json() == {"status": "needs_mfa"}
    assert owner.post("/api/sources/garmin/mfa", json={"code": "123456"}).json() == {"status": "connected"}

    accounts = Accounts(data_dir())
    cred = accounts.credential(1, "garmin")
    assert cred["tokens"] == '{"di_token": "t"}' and "password" not in cred
    assert b"hunter2" not in (data_dir() / "accounts.db").read_bytes()
    g = owner.get("/api/settings").json()["sources"]["garmin"]
    assert g["available"] and g["connected"]

    other = sign_in("friend@example.com", "another long password")
    assert other.post("/api/sources/garmin", json={"email": "x@y.z", "password": "p"}).status_code == 403
    assert other.post("/api/sync", json={"source": "garmin"}).status_code == 403
    assert not other.get("/api/settings").json()["sources"]["garmin"]["available"]

    assert owner.delete("/api/sources/garmin").json() == {"ok": True}
    assert Accounts(data_dir()).credential(1, "garmin") is None


def _health_export(tmp_path, with_route=True) -> bytes:
    """A tiny Health app export: one run with a route and heart rate, one treadmill run, nights."""
    hr = "".join(
        f'<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" '
        f'startDate="2026-10-01 08:{m:02d}:00 -0700" endDate="2026-10-01 08:{m:02d}:00 -0700" value="150"/>'
        for m in range(0, 10))
    hr += "".join(
        f'<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min" '
        f'startDate="2026-10-03 18:{m:02d}:00 -0700" endDate="2026-10-03 18:{m:02d}:00 -0700" value="140"/>'
        for m in range(0, 30, 2))
    route = ('<WorkoutRoute sourceName="Watch"><FileReference path="/workout-routes/route_2026-10-01_8.00am.gpx"/>'
             "</WorkoutRoute>") if with_route else ""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_US">
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Watch" unit="count/min"
   startDate="2026-10-02 00:00:00 -0700" endDate="2026-10-02 23:59:00 -0700" value="47"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Watch"
   startDate="2026-10-01 23:00:00 -0700" endDate="2026-10-02 06:30:00 -0700" value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="iPhone"
   startDate="2026-10-01 22:30:00 -0700" endDate="2026-10-02 06:45:00 -0700" value="HKCategoryValueSleepAnalysisInBed"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="iPhone"
   startDate="2026-10-01 23:30:00 -0700" endDate="2026-10-02 06:00:00 -0700" value="HKCategoryValueSleepAnalysisAsleepUnspecified"/>
 {hr}
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="10" durationUnit="min" sourceName="Watch"
   startDate="2026-10-01 08:00:00 -0700" endDate="2026-10-01 08:10:00 -0700">
  <MetadataEntry key="HKElevationAscended" value="18000 cm"/>
  <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning" sum="1.24" unit="mi"/>
  {route}
 </Workout>
 <Workout workoutActivityType="HKWorkoutActivityTypeRunning" duration="30" durationUnit="min" sourceName="Watch"
   startDate="2026-10-03 18:00:00 -0700" endDate="2026-10-03 18:30:00 -0700">
  <WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning" sum="5" unit="km"/>
 </Workout>
 <Workout workoutActivityType="HKWorkoutActivityTypeYoga" sourceName="Watch"
   startDate="2026-10-04 18:00:00 -0700" endDate="2026-10-04 18:30:00 -0700"/>
</HealthData>"""
    pts = "".join(
        f'<trkpt lat="{37 + i * 0.00003:.6f}" lon="-118.0"><ele>{2000 + i * 0.3:.1f}</ele>'
        f"<time>2026-10-01T15:{i // 60:02d}:{i % 60:02d}Z</time></trkpt>" for i in range(600))
    gpx = ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
           + pts + "</trkseg></trk></gpx>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml)
        zf.writestr("apple_health_export/workout-routes/route_2026-10-01_8.00am.gpx", gpx)
    return buf.getvalue()


def test_apple_health_export(tmp_path):
    from talus.parse import parse

    path = tmp_path / "export.zip"
    path.write_bytes(_health_export(tmp_path))
    ex = read_export(path)
    assert ex.skipped == 1  # yoga: no route and no heart rate
    run, treadmill = ex.activities
    act, raw = run
    assert act.start_time == datetime(2026, 10, 1, 8, 0)  # the wall-clock time where it happened
    assert act.distance_m == pytest.approx(1.24 * 1609.344)
    assert act.gain_m == pytest.approx(180)
    s = parse(raw)
    assert s.heart_rate is not None and s.heart_rate.mean() == pytest.approx(150)
    assert s.altitude[-1] == pytest.approx(2000 + 599 * 0.3)

    t = parse(treadmill[1])
    assert t.distance[-1] == pytest.approx(5000 * 28 / 30, rel=0.01)  # spread evenly to the last sample
    assert t.heart_rate.mean() == pytest.approx(140)

    [night] = [w for w in ex.wellness if w.day == date(2026, 10, 2)]
    assert night.resting_hr == 47
    assert night.sleep_s == 7.5 * 3600  # the watch's night, not the phone's added to it
    assert night.hrv is None


def test_upload_files_zip_batches_and_health_export(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    cli.main(["thresholds", "--resting", "45", "--ant", "172", "--max", "190"])
    client = sign_in()

    r = client.post("/api/import?name=run.gpx", content=_gpx()).json()
    assert (r["new"], r["saved"], r["error"]) == (1, 1, None)
    again = client.post("/api/import?name=run.gpx", content=_gpx()).json()
    assert again["new"] == 0  # the same file twice

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.gpx", _gpx().replace(b"2026-10-01", b"2026-10-05"))
        zf.writestr("b.gpx", _gpx().replace(b"2026-10-01", b"2026-10-06"))
        zf.writestr("notes.txt", "hi")
    r = client.post("/api/import?name=batch.zip", content=buf.getvalue()).json()
    assert (r["listed"], r["new"]) == (2, 2)

    bad = client.post("/api/import?name=x.fit", content=b"not a file").json()
    assert bad["new"] == 0 and bad["failed"]

    store = Store(cli.default_user_dir(Accounts(data_dir())))
    store.save_wellness([WellnessDay("intervals", date(2026, 10, 2), hrv=61, resting_hr=50, sleep_s=25000)])
    # The export's route run is the first upload: files carry UTC, which the engine reads in its
    # own zone, so put that run at the export's wall-clock time as a Mac in California would.
    store.db.execute("UPDATE activity SET start_time = '2026-10-01T08:00:00' WHERE id = 1")
    store.db.commit()
    r = client.post("/api/import?name=export.zip", content=_health_export(tmp_path)).json()
    assert r["source"] == "apple_health"
    assert (r["listed"], r["new"], r["duplicates"], r["no_data"]) == (2, 1, 1, 1)
    w = store.wellness(date(2026, 10, 2), date(2026, 10, 2))[date(2026, 10, 2)]
    assert w["hrv"] == 61 and w["resting_hr"] == 50  # a watch's night is never replaced
    sources = client.get("/api/settings").json()["sources"]
    assert sources["upload"]["last_import"]["source"] == "apple_health"


def _tcx(day: str, hr: int = 145, n: int = 600) -> str:
    pts = "".join(
        f"<Trackpoint><Time>{day}T15:{i // 60:02d}:{i % 60:02d}Z</Time><Position><LatitudeDegrees>{37 + i * 0.00003:.6f}"
        f"</LatitudeDegrees><LongitudeDegrees>-118.0</LongitudeDegrees></Position><AltitudeMeters>{2000 + i * 0.3:.1f}"
        f"</AltitudeMeters><DistanceMeters>{i * 3.3:.1f}</DistanceMeters><HeartRateBpm><Value>{hr}</Value></HeartRateBpm>"
        "</Trackpoint>" for i in range(n))
    return ('<?xml version="1.0"?><TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
            f'<Activities><Activity Sport="Running"><Lap><Track>{pts}</Track></Lap></Activity></Activities></TrainingCenterDatabase>')


def test_healthkit_endpoint_for_the_ios_app(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    cli.main(["thresholds", "--resting", "45", "--ant", "172", "--max", "190"])
    client = sign_in()
    client.post("/api/import?name=run.gpx", content=_gpx().replace(b"2026-10-01", b"2026-10-05"))
    store = Store(cli.default_user_dir(Accounts(data_dir())))
    store.db.execute("UPDATE activity SET start_time = '2026-10-05T08:00:00'")
    store.db.commit()
    store.save_wellness([WellnessDay("intervals", date(2026, 10, 2), hrv=61, resting_hr=50, sleep_s=25000)])

    body = {
        "workouts": [
            {"uuid": "A1", "sport": "trail_run", "name": "Ridge", "start": "2026-10-01T08:00:00-07:00",
             "end": "2026-10-01T08:10:00-07:00", "device_name": "Apple Watch", "tcx": _tcx("2026-10-01")},
            # The run already uploaded as a file, now from HealthKit too.
            {"uuid": "A2", "sport": "run", "start": "2026-10-05T08:00:30-07:00", "end": "2026-10-05T08:10:00-07:00",
             "tcx": _tcx("2026-10-05")},
            {"uuid": "A3", "sport": "walk", "start": "2026-10-06T08:00:00-07:00", "end": "2026-10-06T08:10:00-07:00",
             "tcx": "<TrainingCenterDatabase/>"},
        ],
        "wellness": [{"day": "2026-10-02", "hrv_sdnn_ms": 48.2, "resting_hr": 47, "sleep_s": 26400},
                     {"day": "2026-10-03", "hrv_sdnn_ms": 51.0, "resting_hr": 46, "sleep_s": 27000}],
    }
    r = client.post("/api/import/healthkit", json=body).json()
    assert (r["workouts_added"], r["workouts_skipped"], r["wellness_days"]) == (1, 2, 2)
    assert client.post("/api/import/healthkit", json=body).json()["workouts_added"] == 0  # re-upload

    row = [a for a in store.activities() if a["source"] == "healthkit"][0]
    assert (row["source_id"], row["sport"], row["start_time"], row["name"]) == ("A1", "trail_running", "2026-10-01T08:00:00", "Ridge")
    w = store.wellness(date(2026, 10, 2), date(2026, 10, 3))
    assert w[date(2026, 10, 2)]["hrv"] == 61  # the watch's night stays
    assert w[date(2026, 10, 3)] == {"hrv": None, "resting_hr": 46, "sleep_s": 27000, "sleep_score": None}
    assert client.post("/api/import/healthkit", json={"workouts": [{"uuid": "x", "sport": "swim"}]}).status_code == 422


def _fit_with_local_time(utc_ms: int, offset_s: int) -> bytes:
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.activity_message import ActivityMessage
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.profile_type import FileType, Manufacturer

    b = FitFileBuilder(auto_define=True)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.time_created = utc_ms
    b.add(fid)
    for i in range(300):
        r = RecordMessage()
        r.timestamp = utc_ms + i * 1000
        r.position_lat = 37.0 + i * 0.00003
        r.position_long = -118.0
        r.distance = i * 3.0
        r.altitude = 2000 + i * 0.5
        r.heart_rate = 145
        b.add(r)
    a = ActivityMessage()
    a.timestamp = utc_ms + 300_000
    a.local_timestamp = (utc_ms + 300_000) // 1000 + offset_s - 631065600  # seconds since the FIT epoch
    b.add(a)
    return b.build().to_bytes()


def test_file_imports_file_runs_under_their_local_day(tmp_path, monkeypatch):
    # 11 pm PDT on Oct 1 is 06:00 UTC on Oct 2. The ledger must count it on Oct 1.
    utc = datetime(2026, 10, 2, 6, 0, tzinfo=__import__("datetime").timezone.utc)
    data = _fit_with_local_time(int(utc.timestamp() * 1000), -7 * 3600)
    store = _store(tmp_path, monkeypatch)
    f = tmp_path / "late.fit"
    f.write_bytes(data)
    cli.main(["import", "--no-coach", str(f)])
    [row] = store.activities()
    assert row["start_time"] == "2026-10-01T23:00:00"


def test_rescore_moves_old_utc_file_starts_to_local_time(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    f = tmp_path / "late.fit"
    utc = datetime(2026, 10, 2, 6, 0, tzinfo=__import__("datetime").timezone.utc)
    f.write_bytes(_fit_with_local_time(int(utc.timestamp() * 1000), -7 * 3600))
    cli.main(["import", "--no-coach", str(f)])
    store.db.execute("UPDATE activity SET start_time = '2026-10-02T06:00:00+00:00'")  # how older imports stored it
    store.db.commit()
    cli.main(["rescore"])
    assert store.activities()[0]["start_time"] == "2026-10-01T23:00:00"


def test_garmin_sync_from_the_cli_is_owner_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    sign_in()
    sign_in("friend@example.com", "another long password")
    monkeypatch.setenv("GARMIN_EMAIL", "owner@garmin.example")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    called = []
    monkeypatch.setattr(garmin_mod, "start_login", lambda *a: called.append(a))
    # Even with Garmin tokens saved for the friend somehow, the CLI refuses.
    Accounts(data_dir()).set_credential(2, "garmin", "tokens", {"tokens": "{}"})
    with pytest.raises(SystemExit, match="owner account only"):
        cli.main(["--user", "friend@example.com", "sync", "--source", "garmin", "--no-coach"])
    assert called == []
