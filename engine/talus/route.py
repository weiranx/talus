"""An objective's numbers from a GPX route: distance, gain, loss, longest continuous climb and
descent, and high point. Planned routes usually have no timestamps, so the elevation is cleaned
as GPS elevation against distance rather than time. The track never leaves the engine."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np

from . import elevation
from .loads import effort_miles
from .parse import _find, _local, _num, haversine_cumulative
from .units import ft, mi

ASSUMED_SPEED_MPS = 1.5  # only spaces the samples for the elevation filter's time windows


def route_numbers(data: bytes) -> dict:
    root = ET.fromstring(data)
    pts = [el for el in root.iter() if _local(el.tag) in ("trkpt", "rtept")]
    if len(pts) < 2:
        raise ValueError("The GPX file has no track or route points.")
    lat = np.array([float(p.get("lat")) for p in pts])
    lon = np.array([float(p.get("lon")) for p in pts])
    ele = np.array([_num(_find(p, "ele")) for p in pts])
    dist = haversine_cumulative(lat, lon)
    if np.isnan(ele).all():
        raise ValueError("The GPX file has no elevation, so gain and loss are unknown. Enter them by hand.")
    ok = ~np.isnan(ele)
    ele = np.interp(dist, dist[ok], ele[ok])
    alt, threshold, _ = elevation.clean(ele, barometric=False, time=dist / ASSUMED_SPEED_MPS)
    gain, loss, _, _ = elevation.hysteresis(alt, threshold)
    _, _, climbs, descents = elevation.hysteresis(alt, elevation.LONG_CLIMB_HYSTERESIS_M)
    miles, gain_ft = mi(float(dist[-1])), ft(float(gain.sum()))
    name = next((el.text.strip() for el in root.iter() if _local(el.tag) == "name" and el.text), None)
    return {"name": name, "distance_mi": miles, "gain_ft": gain_ft, "loss_ft": ft(float(loss.sum())),
            "effort_mi": effort_miles(miles, gain_ft),
            "longest_climb_ft": ft(max(climbs, default=0.0)), "longest_descent_ft": ft(max(descents, default=0.0)),
            "high_point_ft": ft(float(alt.max()))}
