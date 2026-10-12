"""Reading a Garmin watch's own GARMIN folder: activities plus overnight HRV, resting HR and sleep."""

from datetime import date, datetime, timedelta, timezone

from talus.cli import main
from talus.sources.base import WellnessDay
from talus.store import Store
from talus.watch import WatchWellness, fit_file_type

FIT_EPOCH_S = 631065600
PDT = -7 * 3600


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _builder(file_type, created: datetime):
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.profile_type import Manufacturer

    b = FitFileBuilder(auto_define=True)
    fid = FileIdMessage()
    fid.type = file_type
    fid.manufacturer = Manufacturer.GARMIN.value
    fid.time_created = _ms(created)
    b.add(fid)
    return b


def sleep_fit(bed: datetime, stages: list[tuple[str, int]], score: int) -> bytes:
    """A night as the watch writes it: a sleep_level sample per stage change, then a score."""
    from fit_tool.profile.messages.sleep_assessment_message import SleepAssessmentMessage
    from fit_tool.profile.messages.sleep_level_message import SleepLevelMessage
    from fit_tool.profile.profile_type import FileType

    levels = {"unmeasurable": 0, "awake": 1, "light": 2, "deep": 3, "rem": 4}
    b = _builder(FileType.MONITORING_DAILY, bed)  # the watch's sleep files use a type fit-tool lacks
    t = bed
    for level, minutes in stages + [("awake", 0)]:
        m = SleepLevelMessage()
        m.timestamp = _ms(t)
        m.sleep_level = levels[level]
        b.add(m)
        t += timedelta(minutes=minutes)
    a = SleepAssessmentMessage()
    a.overall_sleep_score = score
    b.add(a)
    return b.build().to_bytes()


def hrv_fit(at: datetime, rmssd: float) -> bytes:
    from fit_tool.profile.messages.hrv_status_summary_message import HrvStatusSummaryMessage
    from fit_tool.profile.profile_type import FileType

    b = _builder(FileType.MONITORING_DAILY, at)
    m = HrvStatusSummaryMessage()
    m.timestamp = _ms(at)
    m.last_night_average = rmssd
    m.weekly_average = rmssd
    b.add(m)
    return b.build().to_bytes()


def monitor_fit(at: datetime, resting: int, offset_s: int = PDT) -> bytes:
    from fit_tool.profile.messages.monitoring_hr_data_message import MonitoringHrDataMessage
    from fit_tool.profile.messages.monitoring_info_message import MonitoringInfoMessage
    from fit_tool.profile.profile_type import FileType

    b = _builder(FileType.MONITORING_B, at)
    info = MonitoringInfoMessage()
    info.timestamp = _ms(at)
    info.local_timestamp = int(at.timestamp()) + offset_s - FIT_EPOCH_S
    b.add(info)
    m = MonitoringHrDataMessage()
    m.timestamp = _ms(at)
    m.resting_heart_rate = resting + 2  # the 7-day average
    m.current_day_resting_heart_rate = resting
    b.add(m)
    return b.build().to_bytes()


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def test_overnight_files_become_the_morning_you_woke_up():
    w = WatchWellness()
    # Bed 10:30 pm PDT Oct 4 is 05:30 UTC Oct 5; awake 6:30 am PDT Oct 5.
    assert w.add(sleep_fit(utc(2026, 10, 5, 5, 30), [("light", 60), ("deep", 90), ("awake", 15),
                                                      ("rem", 120), ("light", 195)], score=81))
    assert w.add(hrv_fit(utc(2026, 10, 5, 13, 31), 58.5))
    # Resting HR written at 4:50 pm PDT Oct 5, 23:50 UTC; the file says the watch is on PDT.
    assert w.add(monitor_fit(utc(2026, 10, 5, 23, 50), 47))
    # At 5 pm PDT the UTC date is already Oct 6; the watch's offset keeps it on Oct 5.
    assert w.add(monitor_fit(utc(2026, 10, 6, 0, 10), 46))
    assert w.days() == [WellnessDay("watch", date(2026, 10, 5), hrv=58.5, resting_hr=46.0,
                                    sleep_s=(60 + 90 + 120 + 195) * 60, sleep_score=81.0)]


def test_files_without_overnight_data_are_not_wellness():
    from fit_tool.profile.profile_type import FileType
    data = _builder(FileType.TOTALS, utc(2026, 10, 5)).build().to_bytes()
    assert fit_file_type(data) == "totals"
    assert not WatchWellness().add(data)


def test_import_reads_a_copied_garmin_folder(tmp_path, monkeypatch, capsys):
    from tests.test_connectors import _fit_with_local_time

    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    garmin = tmp_path / "GARMIN"
    for folder, name, data in [
        ("Activity", "2026-10-05-07-00-00.fit", _fit_with_local_time(_ms(utc(2026, 10, 5, 14)), PDT)),
        ("Sleep", "S6A50000.FIT", sleep_fit(utc(2026, 10, 5, 5, 30), [("deep", 420)], score=77)),
        ("HRVStatus", "H6A50000.FIT", hrv_fit(utc(2026, 10, 5, 13, 31), 61)),
        ("Monitor", "M6A50000.FIT", monitor_fit(utc(2026, 10, 5, 23, 50), 48)),
        ("Settings", "Settings.fit", _builder(__import__("fit_tool.profile.profile_type", fromlist=["x"])
                                              .FileType.SETTINGS, utc(2026, 10, 5)).build().to_bytes()),
        ("Apps", "Weather.PRG", b"not a fit file"),
    ]:
        (garmin / folder).mkdir(parents=True, exist_ok=True)
        (garmin / folder / name).write_bytes(data)
    main(["thresholds", "--resting", "50", "--max", "190", "--ant", "170"])
    main(["import", str(garmin), "--no-coach"])
    out = capsys.readouterr().out
    assert "1 days of overnight" in out and "1 activities read, 1 new" in out
    from talus.accounts import Accounts
    from talus.cli import default_user_dir
    from talus.config import data_dir
    store = Store(default_user_dir(Accounts(data_dir())))
    assert store.wellness(date(2026, 10, 5), date(2026, 10, 5))[date(2026, 10, 5)] == {
        "hrv": 61.0, "resting_hr": 48.0, "sleep_s": 25200.0, "sleep_score": 77.0}
    assert store.settings()["last_import"]["failed"] == []


def test_a_partial_copy_keeps_what_is_stored(tmp_path):
    from talus.imports import import_paths
    store = Store(tmp_path)
    store.save_wellness([WellnessDay("garmin", date(2026, 10, 5), hrv=55, resting_hr=49, sleep_s=25000)])
    f = tmp_path / "S.fit"
    f.write_bytes(sleep_fit(utc(2026, 10, 5, 5, 30), [("light", 400)], score=70))
    import_paths(store, [("S.fit", f)], log=lambda *_: None)
    assert store.wellness(date(2026, 10, 5), date(2026, 10, 5))[date(2026, 10, 5)] == {
        "hrv": 55, "resting_hr": 49, "sleep_s": 24000.0, "sleep_score": 70.0}


def test_sync_method_and_watch_file_keys(tmp_path, monkeypatch):
    from tests.conftest import sign_in

    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    main(["thresholds", "--resting", "50", "--max", "190", "--ant", "170"])
    client = sign_in()
    assert client.get("/api/settings").json()["sync_method"] == "intervals"
    assert client.put("/api/settings/sync-method", json={"method": "watch"}).status_code == 200
    assert client.put("/api/settings/sync-method", json={"method": "strava"}).status_code == 422
    assert client.get("/api/settings").json()["sync_method"] == "watch"

    assert client.get("/api/import/watch").json() == {"files": [], "last_sync": None}
    hrv = hrv_fit(utc(2026, 10, 5, 13, 31), 61)
    r = client.post("/api/import?name=hrv.fit&watch_file=68-1-1791000000", content=hrv).json()
    assert r["wellness_days"] == 1
    client.post("/api/import?name=bad.fit&watch_file=4-9-1", content=b"not a file")  # unreadable: not kept
    w = client.get("/api/import/watch").json()
    assert w["files"] == ["68-1-1791000000"] and w["last_sync"]
    assert client.get("/api/settings").json()["sources"]["watch"]["last_sync"] == w["last_sync"]
