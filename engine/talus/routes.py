"""Match recorded outdoor routes to today's engine-owned session, without an AI call.

Only GPS-backed activities qualify. Recovery limits exclude routes before ranking; a
near match never overrides them. Geometry stays in the account's existing track cache.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

import numpy as np

from . import heatmap

RUN = {"run", "running", "trailrun", "trailrunning", "streetrunning", "trackrunning"}
HIKE = {"hike", "hiking", "walk", "walking", "mountaineering", "trekking"}
CALLS = ("go", "modify", "rest")


def on_foot(sport: str | None) -> bool:
    # GPX carries no sport; like the planner, treat unspecified imports as on foot.
    return sport is None or sport.lower().replace("_", "").replace(" ", "") in RUN | HIKE


def _measured(a: dict) -> bool:
    values = [a[k] for k in ("distance_mi", "gain_ft", "loss_ft", "effort_mi", "moving_time_s")]
    return (all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values)
            and a["distance_mi"] >= 0.5 and a["moving_time_s"] > 0 and a["effort_mi"] > 0
            and not any(note.startswith("No altitude data") for note in a.get("notes", [])))


def target(today: dict, requested: str | None = None) -> dict | None:
    rule = today["call"]["call"]
    call = max((rule, requested or rule), key=CALLS.index)
    planned = today.get("planned")
    if call == "rest" or (planned and planned["kind"] == "rest"):
        return None
    base = {"call": call, "kind": "easy", "miles": None, "gain_ft": None, "loss_ft": None,
            "min_minutes": 30, "max_minutes": 60, "max_gain_ft": None, "max_loss_ft": None}
    if call == "modify":
        # A coach downgrading a go day uses cautious_session(), not the original plan.
        legs = today["readiness"]["legs"]["state"] != "fresh"
        engine = today["readiness"]["engine"]["state"] != "fresh"
        if rule == "modify" and legs and not engine:
            return base | {"min_minutes": 60, "max_minutes": 75, "max_loss_ft": 500,
                           "text": "Easy aerobic, 60–75 min with less than 500 ft of descent."}
        flat = legs or call != rule
        return base | {"min_minutes": 30 if not legs or call != rule else 15, "max_minutes": 45,
                       "max_gain_ft": 300 if flat else None, "max_loss_ft": 300 if flat else None,
                       "text": "Short and easy, up to 45 min" + (" on flat ground." if flat else "; hike the hills.")}
    if planned:
        minutes = planned.get("minutes") or 0
        return base | {"kind": planned["kind"], "miles": planned["miles"] or None,
                       "gain_ft": planned["gain_ft"], "loss_ft": planned["loss_ft"],
                       "min_minutes": minutes * 0.75 if minutes else None,
                       "max_minutes": minutes * 1.25 if minutes else None,
                       "text": "Matched to today's scheduled distance, climbing and descent."}
    return base | {"text": "No run scheduled: familiar routes for 30–60 min easy."}


def _shape(line: str) -> np.ndarray | None:
    try:
        pts = np.array(heatmap.decode(line))
        if len(pts) < 2 or not np.isfinite(pts).all() or np.any(np.abs(pts[:, 0]) > 90) or np.any(np.abs(pts[:, 1]) > 180):
            return None
        delta = np.diff(pts, axis=0) * [111_320, 111_320 * math.cos(math.radians(float(pts[0, 0])))]
        dist = np.r_[0, np.cumsum(np.linalg.norm(delta, axis=1))]
        if dist[-1] < 100:
            return None
        at = np.linspace(0, dist[-1], 40)
        return np.column_stack([np.interp(at, dist, pts[:, k]) for k in (0, 1)])
    except (ValueError, IndexError, TypeError):
        return None


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    scale = [111_320, 111_320 * math.cos(math.radians(float(a[0, 0])))]
    return any(float(np.mean(np.linalg.norm((a - other) * scale, axis=1))) < 100 for other in (b, b[::-1]))


def recommend(activities: list[dict], lines: dict[int, str | None], today: dict,
              requested: str | None = None) -> dict:
    aim = target(today, requested)
    result = {"target": aim, "routes": [], "status": "rest", "message": "No route to chase today. Follow today's rest call."}
    if aim is None:
        return result
    day = date.fromisoformat(today["date"])
    if any(a["day"] == day.isoformat() and on_foot(a["sport"]) and a["distance_mi"] > 0 for a in activities) and not today.get("planned"):
        return result | {"target": None, "status": "done", "message": "You've already been out today. Routes will be here for your next session."}
    hike = aim["kind"] == "hike"
    history = [a for a in activities if a["day"] < day.isoformat() and on_foot(a["sport"]) and _measured(a)
               and ((a["sport"] or "").lower().replace("_", "").replace(" ", "") in HIKE) == hike]
    # Easy-run pace avoids recommending a route that only fits because it was raced last time.
    easy = [a for a in history if a["day"] >= (day - timedelta(days=90)).isoformat()
            and a["effort_mi"] > 0 and a["moving_time_s"] > 0
            and sum(a["zone_minutes"].values()) > 0
            and a["zone_minutes"]["z1"] / sum(a["zone_minutes"].values()) >= 0.75]
    pace = median(a["moving_time_s"] / 60 / a["effort_mi"] for a in easy) if len(easy) >= 3 else None
    candidates = []
    for a in history:
        if not lines.get(a["id"]):
            continue
        minutes = a["effort_mi"] * pace if pace else a["moving_time_s"] / 60
        if (aim["max_gain_ft"] is not None and a["gain_ft"] > aim["max_gain_ft"]
                or aim["max_loss_ft"] is not None and a["loss_ft"] >= aim["max_loss_ft"]
                or aim["call"] == "modify" and minutes > aim["max_minutes"]):
            continue
        errors, reasons = [], []
        for key, floor, label in (("miles", 1, "distance"), ("gain_ft", 300, "climbing"), ("loss_ft", 300, "descent")):
            expected = aim[key]
            if expected is None:
                continue
            actual = a["distance_mi"] if key == "miles" else a[key]
            error = abs(actual - expected) / max(expected, floor)
            # Flat sessions must not quietly become mountain runs; zero vert is meaningful.
            if error > (0.35 if key == "miles" else 0.75):
                break
            errors.append(error)
            if error <= 0.2:
                reasons.append(f"Close to today's {label} target.")
        else:
            lo, hi = aim["min_minutes"], aim["max_minutes"]
            if lo is not None and hi is not None:
                if minutes < lo or minutes > hi:
                    continue
                errors.append(abs(minutes - (lo + hi) / 2) / max((lo + hi) / 2, 1))
            if aim["max_loss_ft"] is not None:
                reasons.append(f"Keeps descent below {aim['max_loss_ft']:,} ft for today's easier call.")
            if not reasons:
                reasons.append("Fits today's time window." if lo is not None else "Fits today's distance and terrain targets.")
            shape = _shape(lines[a["id"]])
            if shape is None:
                continue
            candidates.append((sum(errors) / max(len(errors), 1), a, minutes, reasons[:2], shape))
    picked = []
    for _, a, minutes, reasons, shape in sorted(candidates, key=lambda c: (c[0], -date.fromisoformat(c[1]["day"]).toordinal(), c[1]["id"])):
        if any(abs(a["distance_mi"] - prior["distance_mi"]) / prior["distance_mi"] < 0.1 and _same(shape, prior_shape)
               for prior, prior_shape in picked):
            continue
        picked.append((a, shape))
        result["routes"].append({"activity_id": a["id"], "name": a["name"] or "Past route", "last_done": a["day"],
                                 "distance_mi": a["distance_mi"], "gain_ft": a["gain_ft"], "loss_ft": a["loss_ft"],
                                 "minutes": round(minutes), "time_basis": "easy_pace" if pace else "previous_activity",
                                 "reasons": reasons})
        if len(picked) == 3:
            break
    if picked:
        return result | {"status": "ready", "message": "From your recorded routes. Open one to see its map."}
    return result | {"status": "empty", "message": "No recorded route fits today's session yet. Choose a route within the targets above, or import more outdoor activities in Settings."}
