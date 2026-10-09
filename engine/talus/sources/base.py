from __future__ import annotations

import gzip
import io
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass
class RemoteActivity:
    source: str
    source_id: str
    start_time: datetime
    name: str | None = None
    sport: str | None = None
    distance_m: float | None = None
    gain_m: float | None = None
    device_name: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class WellnessDay:
    """One morning's overnight record. Platforms file last night's sleep and HRV under the day
    you woke up, and so does Talus."""

    source: str
    day: date
    hrv: float | None = None  # overnight rMSSD, ms
    resting_hr: float | None = None  # bpm
    sleep_s: float | None = None  # seconds asleep
    sleep_score: float | None = None  # the platform's own 0 to 100 score, kept but not used


@dataclass
class RawFile:
    data: bytes
    fmt: str  # "fit", "gpx", "tcx" or "icu-streams" (intervals.icu streams JSON)


class Source(Protocol):
    name: str

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]: ...

    def fetch(self, activity: RemoteActivity) -> RawFile | None: ...

    # Optional: sources that also serve overnight health data define
    # list_wellness(since, until=None) -> list[WellnessDay].


def unwrap(data: bytes) -> bytes:
    """Strip gzip or single-file zip wrappers that platforms put around activity files."""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            return zf.read(names[0])
    return data


def sniff_format(data: bytes) -> str:
    if len(data) >= 12 and data[8:12] == b".FIT":
        return "fit"
    head = data[:2048].lower()
    if b"<gpx" in head:
        return "gpx"
    if b"<trainingcenterdatabase" in head:
        return "tcx"
    raise ValueError("Unrecognized activity file format")
