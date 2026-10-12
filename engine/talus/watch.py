"""Overnight data read straight off a Garmin watch's own files.

Plugged into a computer, a fenix or Forerunner shows up as a USB (MTP) device with a GARMIN folder
of FIT files: Activity holds workouts, and Sleep, HRVStatus and Monitor hold what Garmin Connect
shows as last night's sleep, overnight HRV and resting HR. Activities go through the normal file
import. This module turns the overnight files into WellnessDay records, so a copy of the watch's
GARMIN folder (or a zip of it) gives Talus everything without Garmin's servers.
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import fitdecode

from .sources.base import WellnessDay

SOURCE = "watch"
WELLNESS_MESSAGES = {"hrv_status_summary", "sleep_level", "sleep_assessment", "monitoring_hr_data"}
ASLEEP = {"light", "deep", "rem"}
FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)


def fit_file_type(data: bytes) -> str | None:
    """The file_id type ("activity", "monitoring_b", "sleep", ...), read from the first message."""
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as fit:
            for frame in fit:
                if isinstance(frame, fitdecode.FitDataMessage) and frame.name == "file_id":
                    value = frame.get_value("type", fallback=None)
                    return None if value is None else str(value)
    except Exception:
        return None
    return None


class WatchWellness:
    """Collects overnight records from many FIT files, then merges them into one record per day."""

    def __init__(self) -> None:
        self.hrv: dict[date, float] = {}
        self.resting_hr: dict[date, float] = {}
        self.sleep_s: dict[date, float] = {}
        self.sleep_score: dict[date, float] = {}
        self.offset: timedelta | None = None  # the watch's UTC offset, once any file tells us
        self._pending: list[dict] = []  # rows held until every file is read, so any offset applies

    def add(self, data: bytes) -> bool:
        """Read one FIT file. False when it holds none of the overnight messages."""
        rows: dict[str, list[dict]] = defaultdict(list)
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.DISABLED) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name in WELLNESS_MESSAGES:
                    rows[frame.name].append({f.name: f.value for f in frame.fields if f.value is not None})
                elif frame.name == "monitoring_info" and self.offset is None:
                    self.offset = _offset(frame)
        if not rows:
            return False
        self._pending.append(rows)
        return True

    def days(self) -> list[WellnessDay]:
        for rows in self._pending:
            self._read(rows)
        self._pending.clear()
        out = []
        for day in sorted(set(self.hrv) | set(self.resting_hr) | set(self.sleep_s) | set(self.sleep_score)):
            out.append(WellnessDay(SOURCE, day, hrv=self.hrv.get(day), resting_hr=self.resting_hr.get(day),
                                   sleep_s=self.sleep_s.get(day), sleep_score=self.sleep_score.get(day)))
        return out

    def _local(self, ts) -> datetime:
        ts = _as_utc(ts)
        if self.offset is not None:
            return (ts + self.offset).replace(tzinfo=None)
        return ts.astimezone().replace(tzinfo=None)  # the engine's zone, as for GPX and TCX files

    def _read(self, rows: dict[str, list[dict]]) -> None:
        for r in rows.get("hrv_status_summary", []):
            if r.get("last_night_average") and r.get("timestamp"):
                self.hrv[self._local(r["timestamp"]).date()] = float(r["last_night_average"])
        for r in rows.get("monitoring_hr_data", []):
            bpm = r.get("current_day_resting_heart_rate") or r.get("resting_heart_rate")
            if bpm and r.get("timestamp"):
                self.resting_hr[self._local(r["timestamp"]).date()] = float(bpm)  # the day's latest wins
        levels = sorted((_as_utc(r["timestamp"]), str(r.get("sleep_level")))
                        for r in rows.get("sleep_level", []) if r.get("timestamp"))
        if len(levels) >= 2:
            # Each sample starts a stretch that lasts until the next one. The night is filed under
            # the morning you woke up, like every other source.
            asleep = sum((b[0] - a[0]).total_seconds() for a, b in zip(levels, levels[1:]) if a[1] in ASLEEP)
            woke = self._local(levels[-1][0]).date()
            self.sleep_s[woke] = asleep
            for r in rows.get("sleep_assessment", []):
                if r.get("overall_sleep_score") is not None:
                    self.sleep_score[woke] = float(r["overall_sleep_score"])


def _as_utc(ts) -> datetime:
    if isinstance(ts, (int, float)):
        return FIT_EPOCH + timedelta(seconds=ts)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _offset(frame) -> timedelta | None:
    """monitoring_info carries both UTC and local time: their difference is the watch's offset."""
    ts, local = frame.get_value("timestamp", fallback=None), frame.get_value("local_timestamp", fallback=None)
    if ts is None or local is None:
        return None
    if isinstance(local, datetime):
        local = (local.replace(tzinfo=timezone.utc) - FIT_EPOCH).total_seconds()
    offset = timedelta(seconds=round((FIT_EPOCH + timedelta(seconds=local) - _as_utc(ts)).total_seconds() / 900) * 900)
    return offset if abs(offset) <= timedelta(hours=14) else None
