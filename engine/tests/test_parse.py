import gzip
import json

import numpy as np
import pytest

from talus.parse import parse
from talus.sources.base import RawFile, sniff_format, unwrap


def _gpx(n=600):
    pts = []
    for i in range(n):
        lat = 37.0 + i * 0.00003  # ~3.3 m per second, northbound
        pts.append(
            f'<trkpt lat="{lat:.6f}" lon="-118.0"><ele>{2000 + i * 0.3:.1f}</ele>'
            f"<time>2026-10-01T15:{i // 60:02d}:{i % 60:02d}Z</time>"
            f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{150}</gpxtpx:hr>"
            f"<gpxtpx:cad>85</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions></trkpt>"
        )
    return ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>'
            + "".join(pts) + "</trkseg></trk></gpx>").encode()


def test_gpx():
    data = _gpx()
    assert sniff_format(data) == "gpx"
    s = parse(RawFile(data, "gpx"))
    assert s.time[-1] == 599
    assert s.distance[-1] == pytest.approx(599 * 3.336, rel=0.01)
    assert s.altitude[-1] == pytest.approx(2000 + 599 * 0.3)
    assert np.all(s.heart_rate == 150)
    assert np.all(s.cadence == 170)  # per-foot cadence doubled to steps per minute


def test_fit():
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.profile_type import FileType, Manufacturer

    builder = FitFileBuilder(auto_define=True)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.time_created = 1790000000000
    builder.add(fid)
    for i in range(300):
        r = RecordMessage()
        r.timestamp = 1790000000000 + i * 1000
        r.position_lat = 37.0 + i * 0.00003
        r.position_long = -118.0
        r.distance = i * 3.0
        r.altitude = 2000 + i * 0.5
        r.heart_rate = 145
        r.cadence = 84
        builder.add(r)
    data = builder.build().to_bytes()
    assert sniff_format(data) == "fit"
    s = parse(RawFile(data, "fit"))
    assert s.time[-1] == 299
    assert s.distance[-1] == pytest.approx(897, abs=1)
    assert s.altitude[-1] == pytest.approx(2149.5, abs=0.5)
    assert s.lat[0] == pytest.approx(37.0, abs=1e-5)
    assert s.cadence[0] == 168


def test_icu_streams_and_gzip_unwrap():
    streams = [
        {"type": "time", "data": list(range(100))},
        {"type": "distance", "data": [i * 3.0 for i in range(100)]},
        {"type": "altitude", "data": [1000 + i for i in range(100)]},
        {"type": "heartrate", "data": [140] * 100},
        {"type": "cadence", "data": [86] * 100},
        {"type": "latlng", "data": [37.0] * 100, "data2": [-118.0] * 100},
    ]
    data = unwrap(gzip.compress(json.dumps(streams).encode()))
    s = parse(RawFile(data, "icu-streams"))
    assert s.distance[-1] == 297
    assert s.cadence[0] == 172
    assert s.lon[0] == -118.0


def test_heart_rate_dropouts_are_filled():
    streams = [
        {"type": "time", "data": list(range(10))},
        {"type": "distance", "data": [i * 3.0 for i in range(10)]},
        {"type": "heartrate", "data": [140, 141, 0, 0, 144, 145, 146, 147, 148, 149]},
    ]
    s = parse(RawFile(json.dumps(streams).encode(), "icu-streams"))
    assert s.heart_rate[2] == pytest.approx(142)
