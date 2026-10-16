"""An objective's numbers from a GPX route: distance, gain, loss, longest continuous climb and
descent, and high point. Planned routes usually have no timestamps, so the elevation is cleaned
as GPS elevation against distance rather than time.

The route's profile is kept too, for the finish-time estimate (predict.py): altitude and
cumulative gain and loss every PROFILE_STEP_M along the route, and the named waypoints (aid
stations, summits) as miles along it. It holds no coordinates, so the track never leaves the
engine."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np

from . import elevation
from .loads import effort_miles
from .parse import _find, _local, _num, haversine_cumulative
from .units import ft, mi

ASSUMED_SPEED_MPS = 1.5  # only spaces the samples for the elevation filter's time windows
PROFILE_STEP_M = 100.0
STATION_NEAR_M = 250.0  # a waypoint further than this from the route isn't on it
END_MI = 0.1  # a waypoint this close to the start or finish is the start or finish
MAX_STATIONS = 60


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
    # GPX puts a name on the file, the track and each waypoint; the route's is the first not on a waypoint.
    wpt_names = {id(_find(w, "name")) for w in root.iter() if _local(w.tag) == "wpt"}
    name = next((el.text.strip() for el in root.iter() if _local(el.tag) == "name" and el.text
                 and id(el) not in wpt_names), None)
    return {"name": name, "distance_mi": miles, "gain_ft": gain_ft, "loss_ft": ft(float(loss.sum())),
            "effort_mi": effort_miles(miles, gain_ft),
            "longest_climb_ft": ft(max(climbs, default=0.0)), "longest_descent_ft": ft(max(descents, default=0.0)),
            "high_point_ft": ft(float(alt.max())),
            "profile": profile(dist, alt, gain, loss, _stations(root, lat, lon, dist))}


def profile(dist: np.ndarray, alt: np.ndarray, gain: np.ndarray, loss: np.ndarray, stations: list[dict]) -> dict:
    """Altitude and cumulative gain and loss every PROFILE_STEP_M, so each stretch's gain and
    loss add up to the route's own totals."""
    total = float(dist[-1])
    grid = np.arange(0.0, total, PROFILE_STEP_M)
    grid = np.append(grid[grid < total - 1.0], total)  # a last step under a meter joins the one before
    d, idx = np.unique(dist, return_index=True)

    def at(values):
        return [round(float(v), 1) for v in np.interp(grid, d, values[idx])]
    return {"dist_m": [round(float(v), 1) for v in grid], "alt_m": at(alt), "up_m": at(np.cumsum(gain)),
            "down_m": at(np.cumsum(loss)), "stations": stations}


def _stations(root, lat: np.ndarray, lon: np.ndarray, dist: np.ndarray) -> list[dict]:
    """Named waypoints on the route, as miles along it, in order: a race GPX marks its aid stations
    this way. A waypoint near more than one part of the route (a loop) takes the nearest."""
    out = []
    coslat = np.cos(np.radians(float(np.nanmean(lat))))
    total = mi(float(dist[-1]))
    for w in root.iter():
        if _local(w.tag) != "wpt" or w.get("lat") is None or w.get("lon") is None:
            continue
        el = _find(w, "name")
        label = (el.text or "").strip()[:60] if el is not None else ""
        if not label:
            continue
        # Nearest point on each segment, not just each vertex: a sparse route can pass right by a
        # waypoint halfway between two points far apart.
        y = (lat - float(w.get("lat"))) * 111_320.0
        x = (lon - float(w.get("lon"))) * 111_320.0 * coslat
        sx, sy = np.diff(x), np.diff(y)
        seg2 = sx * sx + sy * sy
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.clip(np.where(seg2 > 0, -(x[:-1] * sx + y[:-1] * sy) / seg2, 0.0), 0.0, 1.0)
        near = np.hypot(x[:-1] + t * sx, y[:-1] + t * sy)
        i = int(np.nanargmin(near))
        at = mi(float(dist[i] + t[i] * (dist[i + 1] - dist[i])))
        if near[i] > STATION_NEAR_M or at < END_MI or at > total - END_MI:
            continue
        out.append({"name": label, "mi": round(at, 2)})
    out.sort(key=lambda s: s["mi"])
    return out[:MAX_STATIONS]
