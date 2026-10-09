"""HTTP API over the engine for the web app. Run: uv run talus serve

Every number here comes from the engine; the web app only formats it. All distances are
miles and all vert is feet.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import coach, ledger, wellness
from .config import data_dir, load_env
from .parse import parse
from .readiness import daily_call, readiness
from .score import MAX_DT_S, score
from .store import Store
from .units import ft, mi

MAX_POINTS = 800

app = FastAPI(title="Talus engine")
coach_client = coach.default_client  # swapped out in tests


def get_store() -> Store:
    load_env()
    return Store(data_dir())


def _ledgers_through_today(store: Store) -> None:
    store.save_ledgers(ledger.build(store.scored_loads()))


def _ledger_json(d: ledger.LedgerDay) -> dict:
    return {"day": d.day.isoformat(), "load": d.load, "fitness": d.fitness, "fatigue": d.fatigue,
            "form": d.form, "vs_normal": d.vs_normal, "vs_normal_text": ledger.describe_vs_normal(d.vs_normal),
            "flags": [{"key": f, "text": ledger.FLAG_TEXT[f]} for f in d.flags]}


def _activities(store: Store, since: date | None = None) -> list[dict]:
    out = []
    for row in store.activities():
        if not row["summary"] or not row["start_time"]:
            continue
        day = date.fromisoformat(row["start_time"][:10])
        if since and day < since:
            continue
        m = json.loads(row["summary"])
        out.append({"id": row["id"], "day": day.isoformat(), "start_time": row["start_time"],
                    "name": row["name"], "sport": row["sport"], "source": row["source"],
                    "device_name": row["device_name"], **{k: m[k] for k in (
                        "distance_mi", "gain_ft", "loss_ft", "effort_mi", "moving_time_s", "elapsed_time_s",
                        "cardio_load", "climb_load", "descent_load", "cardio_estimated", "zone_minutes",
                        "hike_share", "notes")}})
    return out


def _thresholds(store: Store) -> dict | None:
    th = store.thresholds()
    if th is None:
        return None
    return {"resting": th.resting, "aet": th.aet, "ant": th.ant, "max": th.max, "source": th.source,
            "aerobic_gap": (th.ant - th.aet) / th.ant}


def _zone_share(acts: list[dict]) -> dict:
    total = {z: sum(a["zone_minutes"][z] for a in acts) for z in ("z1", "z2", "z3")}
    minutes = sum(total.values())
    return {"minutes": total, "share": {z: (v / minutes if minutes else 0.0) for z, v in total.items()}}


@app.get("/api/today")
def today() -> dict:
    store = get_store()
    th = store.thresholds()
    if th is None:
        raise HTTPException(409, "Set thresholds first: talus thresholds")
    _ledgers_through_today(store)
    day = date.today()
    recent = store.ledger_days(day - timedelta(days=41))
    now = {d.ledger: d for d in recent if d.day == day}
    checkin = store.checkin(day)
    overnight = wellness.evaluate(store.wellness(day - timedelta(days=wellness.BASELINE_DAYS - 1), day), day)
    ready = readiness(now, checkin, overnight)
    week_start = day - timedelta(days=day.weekday())
    week = sorted(_activities(store, week_start), key=lambda a: a["start_time"])
    return {
        "date": day.isoformat(),
        "checkin": checkin,
        "wellness": overnight,
        "readiness": ready,
        "call": daily_call(ready, checkin, th.aet) | {"source": "rules"},
        "ledgers": {name: _ledger_json(now[name]) | {"spark": [
            {"day": d.day.isoformat(), "fitness": d.fitness, "fatigue": d.fatigue}
            for d in recent if d.ledger == name]} for name in ledger.LEDGERS if name in now},
        "week": {"start": week_start.isoformat(), "activities": week,
                 "distance_mi": sum(a["distance_mi"] for a in week),
                 "gain_ft": sum(a["gain_ft"] for a in week), "loss_ft": sum(a["loss_ft"] for a in week)},
        "intensity_28d": _zone_share(_activities(store, day - timedelta(days=27))),
        "thresholds": _thresholds(store),
    }


class CheckIn(BaseModel):
    soreness: int = Field(ge=1, le=5)
    energy: int = Field(ge=1, le=5)
    pain: int = Field(ge=1, le=5)


@app.post("/api/checkin")
def checkin(body: CheckIn) -> dict:
    store = get_store()
    store.save_checkin(date.today(), body.soreness, body.energy, body.pain)
    return {"ok": True}


@app.get("/api/load")
def load(days: int = 182) -> dict:
    store = get_store()
    _ledgers_through_today(store)
    day = date.today()
    series = store.ledger_days(day - timedelta(days=days - 1))
    acts = _activities(store, day - timedelta(days=55))
    weeks = []
    for w in range(7, -1, -1):
        start = day - timedelta(days=day.weekday()) - timedelta(weeks=w)
        in_week = [a for a in acts if start.isoformat() <= a["day"] < (start + timedelta(days=7)).isoformat()]
        weeks.append({"start": start.isoformat(), **_zone_share(in_week)})
    return {
        "date": day.isoformat(),
        "ledgers": {name: {"today": _ledger_json([d for d in series if d.ledger == name][-1]) if series else None,
                           "series": [_ledger_json(d) for d in series if d.ledger == name]}
                    for name in ledger.LEDGERS},
        "intensity_by_week": weeks,
        "biggest_days": sorted(acts, key=lambda a: a["effort_mi"], reverse=True)[:5],
        "thresholds": _thresholds(store),
    }


@app.get("/api/activities")
def activities(limit: int = 50) -> list[dict]:
    return _activities(get_store())[:limit]


@app.get("/api/activities/{activity_id}")
def activity(activity_id: int) -> dict:
    store = get_store()
    row = store.activity(activity_id)
    th = store.thresholds()
    if row is None or th is None:
        raise HTTPException(404, "No such activity, or thresholds not set")
    streams = parse(store.raw(row))
    streams.sport = streams.sport or row["sport"]
    result = score(streams, th)
    day = date.fromisoformat(row["start_time"][:10])
    before = {d.ledger: d.fitness for d in store.ledger_days(day - timedelta(days=1))
              if d.day == day - timedelta(days=1)}
    six_weeks = [a for a in _activities(store, day - timedelta(days=42)) if a["day"] <= day.isoformat()]

    def usual(name: str, value: float | None) -> dict:
        others = [a[f"{name}_load"] or 0 for a in six_weeks if a["id"] != activity_id]
        return {"load": value,
                "times_usual_day": None if value is None else ledger.times_usual_day(value, before.get(name, 0.0)),
                "six_week_high": value is not None and bool(others) and value > max(others)}

    hr = streams.heart_rate
    zone = None if hr is None else np.where(hr < th.aet, 1, np.where(hr <= th.ant, 2, 3))
    step = max(1, len(streams.time) // MAX_POINTS)
    idx = np.arange(0, len(streams.time), step)

    def pick(arr, f=lambda x: x, nd=1):
        return None if arr is None else [round(float(f(v)), nd) for v in arr[idx]]

    dt = np.minimum(np.diff(streams.time, prepend=streams.time[0]), MAX_DT_S)
    segments = []
    for n, seg in enumerate(result.segments):
        sl = (streams.time >= seg.start_s) & (streams.time <= seg.end_s)
        z = None if zone is None else {f"z{k}": float(np.sum(dt[sl & (zone == k)]) / 60) for k in (1, 2, 3)}
        segments.append({"index": n, "kind": seg.kind, "distance_mi": mi(seg.distance_m),
                         "gain_ft": ft(seg.gain_m), "loss_ft": ft(seg.loss_m), "avg_grade": seg.avg_grade,
                         "grade_band": seg.grade_band, "hike_share": seg.hike_share,
                         "time_s": seg.end_s - seg.start_s, "start_s": seg.start_s, "end_s": seg.end_s,
                         "zone_minutes": z, "climb_load": seg.climb_load, "descent_load": seg.descent_load})
    return {
        "id": activity_id, "name": row["name"], "day": day.isoformat(), "start_time": row["start_time"],
        "source": row["source"], "device_name": row["device_name"], "sport": streams.sport,
        "distance_mi": result.distance_mi, "gain_ft": result.gain_ft, "loss_ft": result.loss_ft,
        "effort_mi": result.effort_mi, "moving_time_s": result.moving_time_s,
        "elapsed_time_s": result.elapsed_time_s, "hike_share": result.hike_share,
        "zone_minutes": result.zone_minutes, "gain_by_band_ft": result.gain_by_band_ft,
        "loss_by_band_ft": result.loss_by_band_ft, "longest_climb_ft": result.longest_climb_ft,
        "longest_descent_ft": result.longest_descent_ft, "minutes_above_8000ft": result.minutes_above_8000ft,
        "cardio_estimated": result.cardio_estimated, "notes": result.notes,
        "loads": {"cardio": usual("cardio", result.cardio_load), "climb": usual("climb", result.climb_load),
                  "descent": usual("descent", result.descent_load)},
        "segments": segments,
        "streams": {"time_s": pick(streams.time, nd=0), "distance_mi": pick(streams.distance, mi, 3),
                    "altitude_ft": pick(streams.altitude, ft, 0), "heart_rate": pick(hr, nd=0),
                    "zone": None if zone is None else [int(v) for v in zone[idx]],
                    "lat": pick(streams.lat, nd=6), "lon": pick(streams.lon, nd=6)},
    }


def _coach(store: Store, mode: str, subject: str, packet: dict, fallback: dict, refresh: bool) -> dict:
    """Reuse the logged reply for the same packet and prompt version, so reloading a page doesn't
    call the API again. Only API calls are logged; with no key the rules are shown as is."""
    h = coach.packet_hash(packet)
    row = None if refresh else store.coach_entry(mode, subject, coach.PROMPT_VERSION, h)
    if row is not None and not (row["fallback_reason"] or "").startswith("api_error"):
        return {"source": row["source"], "reply": json.loads(row["reply"]), "log_id": row["id"],
                "feedback": row["feedback"], "fallback_reason": row["fallback_reason"], "model": row["model"]}
    result = coach.run(mode, packet, fallback, coach_client())
    log_id = None
    if result.fallback_reason != "no_key":
        log_id = store.log_coach(mode, subject, coach.PROMPT_VERSION, h, packet, result)
    return {"source": result.source, "reply": result.reply, "log_id": log_id, "feedback": None,
            "fallback_reason": result.fallback_reason, "model": result.model}


@app.get("/api/coach/brief")
def coach_brief(refresh: bool = False) -> dict:
    t = today()
    store = get_store()
    day = date.fromisoformat(t["date"])
    packet = coach.brief_packet(t, _activities(store, day - timedelta(days=13)))
    fallback = {k: t["call"][k] for k in ("call", "session", "detail", "why")}
    out = _coach(store, "brief", t["date"], packet, fallback, refresh)
    if out["source"] == "coach":
        out["reply"] = coach.brief_reply(out["reply"], fallback, t["thresholds"]["aet"])
    return out


@app.get("/api/coach/activity/{activity_id}")
def coach_activity(activity_id: int, refresh: bool = False) -> dict:
    a = activity(activity_id)
    store = get_store()
    _ledgers_through_today(store)
    day = date.today()
    now = {d.ledger: _ledger_json(d) for d in store.ledger_days(day) if d.day == day}
    packet = coach.analysis_packet(a, _thresholds(store), now, day)
    return _coach(store, "analysis", str(activity_id), packet, coach.rules_analysis(packet), refresh)


class Feedback(BaseModel):
    rating: int | None = Field(default=None, ge=-1, le=1)


@app.post("/api/coach/feedback/{log_id}")
def coach_feedback(log_id: int, body: Feedback) -> dict:
    if not get_store().coach_feedback(log_id, body.rating or None):
        raise HTTPException(404, "No such coach reply")
    return {"ok": True}
