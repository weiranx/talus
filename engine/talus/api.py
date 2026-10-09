"""HTTP API over the engine for the web app. Run: uv run talus serve

Every number here comes from the engine; the web app only formats it. All distances are
miles and all vert is feet.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import time
import zipfile
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie

import numpy as np
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import cli, coach, explain, ledger, wellness, zones
from . import settings as app_settings
from .accounts import SESSION_DAYS, Account, AccountError, Accounts
from .config import data_dir, load_env
from .models import Thresholds
from .parse import parse
from .readiness import daily_call, readiness
from .score import MAX_DT_S, score
from .store import Store
from .units import ft, mi

MAX_POINTS = 800

app = FastAPI(title="Talus engine")
coach_client = coach.default_client  # swapped out in tests

COOKIE = "talus_session"
_account: ContextVar[Account | None] = ContextVar("talus_account", default=None)


def get_accounts() -> Accounts:
    load_env()
    return Accounts(data_dir())


def current_account() -> Account:
    account = _account.get()
    if account is None:
        raise HTTPException(401, "Sign in first.")
    return account


def get_store() -> Store:
    """The signed-in account's own store. Outside a request (the CLI, tests) it is the owner's,
    the same one `talus` commands use."""
    accounts = get_accounts()
    account = _account.get()
    return Store(accounts.user_dir(account.id) if account else cli.default_user_dir(accounts))


class AuthMiddleware:
    """Every /api route except /api/auth/* needs a signed-in session. The account is set for the
    request, and get_store() opens that account's folder, so no route can reach another
    account's data."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path.startswith("/api/auth/"):
            return await self.inner(scope, receive, send)
        cookie = SimpleCookie()
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                cookie.load(value.decode("latin-1"))
        token = cookie[COOKIE].value if COOKIE in cookie else None
        account = get_accounts().session_account(token)
        if account is None:
            return await JSONResponse({"detail": "Sign in first."}, status_code=401)(scope, receive, send)
        reset = _account.set(account)
        try:
            await self.inner(scope, receive, send)
        finally:
            _account.reset(reset)


app.add_middleware(AuthMiddleware)


def _ledger_json(d: ledger.LedgerDay, since: date | None = None) -> dict:
    """`since` is where the current run of data starts; until it holds MIN_HISTORY_DAYS the
    normal is still being built and reads as such."""
    building = since is not None and (d.day - since).days + 1 < ledger.MIN_HISTORY_DAYS
    return {"day": d.day.isoformat(), "load": d.load, "fitness": d.fitness, "fatigue": d.fatigue,
            "form": d.form, "vs_normal": d.vs_normal, "vs_normal_text": ledger.describe_vs_normal(d.vs_normal),
            "flags": [{"key": f, "text": ledger.FLAG_TEXT[f]} for f in d.flags], "building": building}


def _history(store: Store) -> dict:
    """Where the current run of data starts, and the gap before it if there is one."""
    days = sorted({d.day for d in store.ledgers() if d.ledger == "cardio"})
    if not days:
        return {"since": None, "gap": None}
    span = ledger.current_span([ledger.LedgerDay(d, "cardio", 0, 0, 0) for d in days])
    since = span[0].day
    before = [d for d in days if d < since]
    gap = None if not before else {"from": (before[-1] + timedelta(days=1)).isoformat(),
                                   "to": (since - timedelta(days=1)).isoformat()}
    return {"since": since, "gap": gap}


def _zone_model(store: Store) -> dict | None:
    th = store.thresholds()
    return None if th is None else zones.model(th, store.settings().get("hr_zones"))


def _display_minutes(m: dict, zm: dict | None) -> dict | None:
    """Time in each display zone. Activities scored before the histogram existed fall back to the
    engine split when that is the model in use; saving zone settings rescores, which fills it in."""
    if zm is None:
        return None
    n = zm["zones"]
    if m.get("hr_hist") is not None:
        return zones.minutes(m["hr_hist"], zm["floors"])
    if not sum(m["zone_minutes"].values()):
        return {f"z{k + 1}": 0.0 for k in range(n)}
    if n == 3 and not zm["custom"]:
        return m["zone_minutes"]
    return None


def _activities(store: Store, since: date | None = None) -> list[dict]:
    zm = _zone_model(store)
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
                        "hike_share", "notes")}, "hr_zone_minutes": _display_minutes(m, zm)})
    return out


def _thresholds(store: Store) -> dict | None:
    th = store.thresholds()
    if th is None:
        return None
    return {"resting": th.resting, "aet": th.aet, "ant": th.ant, "max": th.max, "source": th.source,
            "aerobic_gap": (th.ant - th.aet) / th.ant}


def _zone_share(acts: list[dict]) -> dict:
    """The engine's split: z1 below AeT, z2 AeT to AnT, z3 above AnT. The 80% easy check reads z1."""
    total = {z: sum(a["zone_minutes"][z] for a in acts) for z in ("z1", "z2", "z3")}
    minutes = sum(total.values())
    return {"minutes": total, "share": {z: (v / minutes if minutes else 0.0) for z, v in total.items()}}


def _display_share(acts: list[dict], zm: dict | None) -> dict | None:
    """Time in each zone of the zone model chosen in Settings."""
    if zm is None:
        return None
    keys = [f"z{k + 1}" for k in range(zm["zones"])]
    total = {z: sum((a["hr_zone_minutes"] or {}).get(z, 0.0) for a in acts) for z in keys}
    minutes = sum(total.values())
    return {"minutes": total, "share": {z: (v / minutes if minutes else 0.0) for z, v in total.items()}}


def _zone_model_json(zm: dict | None) -> dict | None:
    return None if zm is None else {k: zm[k] for k in ("zones", "custom", "list")}


@app.get("/api/today")
def today() -> dict:
    store = get_store()
    th = store.thresholds()
    if th is None:
        raise HTTPException(409, "Set thresholds first: talus thresholds")
    day = date.today()
    recent = ledger.current_span(store.ledgers(day - timedelta(days=41)))
    since = _history(store)["since"]
    now = {d.ledger: d for d in recent if d.day == day}
    checkin = store.checkin(day)
    overnight = wellness.evaluate(store.wellness(day - timedelta(days=wellness.BASELINE_DAYS - 1), day), day,
                                  wellness.sleep_need(store.settings())[0])
    ready = readiness(now, checkin, overnight)
    week_start = day - timedelta(days=day.weekday())
    week = sorted(_activities(store, week_start), key=lambda a: a["start_time"])
    last_28 = _activities(store, day - timedelta(days=27))
    zm = _zone_model(store)
    return {
        "date": day.isoformat(),
        "checkin": checkin,
        "wellness": overnight,
        "readiness": ready,
        "call": daily_call(ready, checkin, th.aet) | {"source": "rules"},
        "ledgers": {name: _ledger_json(now[name], since) | {"spark": [
            {"day": d.day.isoformat(), "fitness": d.fitness, "fatigue": d.fatigue}
            for d in recent if d.ledger == name]} for name in ledger.LEDGERS if name in now},
        "week": {"start": week_start.isoformat(), "activities": week,
                 "distance_mi": sum(a["distance_mi"] for a in week),
                 "gain_ft": sum(a["gain_ft"] for a in week), "loss_ft": sum(a["loss_ft"] for a in week)},
        "intensity_28d": _zone_share(last_28),
        "zones_28d": _display_share(last_28, zm),
        "zone_model": _zone_model_json(zm),
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
    day = date.today()
    # Charts start after the last gap in the data: months with nothing synced are not drawn.
    series = ledger.current_span(store.ledgers(day - timedelta(days=days - 1)))
    hist = _history(store)
    acts = _activities(store, day - timedelta(days=55))
    zm = _zone_model(store)
    weeks = []
    for w in range(7, -1, -1):
        start = day - timedelta(days=day.weekday()) - timedelta(weeks=w)
        in_week = [a for a in acts if start.isoformat() <= a["day"] < (start + timedelta(days=7)).isoformat()]
        weeks.append({"start": start.isoformat(), **_zone_share(in_week), "zones": _display_share(in_week, zm)})
    return {
        "date": day.isoformat(),
        "ledgers": {name: {"today": _ledger_json([d for d in series if d.ledger == name][-1], hist["since"])
                           if series else None,
                           "series": [_ledger_json(d, hist["since"]) for d in series if d.ledger == name]}
                    for name in ledger.LEDGERS},
        "history": {"since": hist["since"] and hist["since"].isoformat(), "gap": hist["gap"]},
        "intensity_by_week": weeks,
        "zone_model": _zone_model_json(zm),
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
    before = {d.ledger: d.fitness for d in store.ledgers(day - timedelta(days=1))
              if d.day == day - timedelta(days=1)}
    six_weeks = [a for a in _activities(store, day - timedelta(days=42)) if a["day"] <= day.isoformat()]

    def usual(name: str, value: float | None) -> dict:
        others = [a[ledger.ACTIVITY_KEY[name]] or 0 for a in six_weeks if a["id"] != activity_id]
        return {"load": value,
                "times_usual_day": None if value is None else ledger.times_usual_day(value, before.get(name, 0.0)),
                "six_week_high": value is not None and bool(others) and value > max(others)}

    hr = streams.heart_rate
    zm = zones.model(th, store.settings().get("hr_zones"))
    split = None if hr is None else np.where(hr < th.aet, 1, np.where(hr <= th.ant, 2, 3))
    zone = None if hr is None else zones.indices(hr, zm["floors"])
    step = max(1, len(streams.time) // MAX_POINTS)
    idx = np.arange(0, len(streams.time), step)

    def pick(arr, f=lambda x: x, nd=1):
        return None if arr is None else [round(float(f(v)), nd) for v in arr[idx]]

    dt = np.minimum(np.diff(streams.time, prepend=streams.time[0]), MAX_DT_S)
    segments = []
    for n, seg in enumerate(result.segments):
        sl = (streams.time >= seg.start_s) & (streams.time <= seg.end_s)
        z = None if split is None else {f"z{k}": float(np.sum(dt[sl & (split == k)]) / 60) for k in (1, 2, 3)}
        hz = None if zone is None else {f"z{k}": float(np.sum(dt[sl & (zone == k)]) / 60)
                                        for k in range(1, zm["zones"] + 1)}
        segments.append({"index": n, "kind": seg.kind, "distance_mi": mi(seg.distance_m),
                         "gain_ft": ft(seg.gain_m), "loss_ft": ft(seg.loss_m), "avg_grade": seg.avg_grade,
                         "grade_band": seg.grade_band, "hike_share": seg.hike_share,
                         "time_s": seg.end_s - seg.start_s, "start_s": seg.start_s, "end_s": seg.end_s,
                         "zone_minutes": z, "hr_zone_minutes": hz, "climb_load": seg.climb_load,
                         "descent_load": seg.descent_load})
    return {
        "id": activity_id, "name": row["name"], "day": day.isoformat(), "start_time": row["start_time"],
        "source": row["source"], "device_name": row["device_name"], "sport": streams.sport,
        "distance_mi": result.distance_mi, "gain_ft": result.gain_ft, "loss_ft": result.loss_ft,
        "effort_mi": result.effort_mi, "moving_time_s": result.moving_time_s,
        "elapsed_time_s": result.elapsed_time_s, "hike_share": result.hike_share,
        "zone_minutes": result.zone_minutes, "zone_model": _zone_model_json(zm),
        "hr_zone_minutes": zones.minutes(result.hr_hist, zm["floors"]) if hr is not None else None,
        "gain_by_band_ft": result.gain_by_band_ft,
        "loss_by_band_ft": result.loss_by_band_ft, "longest_climb_ft": result.longest_climb_ft,
        "longest_descent_ft": result.longest_descent_ft, "minutes_above_8000ft": result.minutes_above_8000ft,
        "cardio_estimated": result.cardio_estimated, "notes": result.notes,
        "loads": {name: usual(name, getattr(result, key)) for name, key in ledger.ACTIVITY_KEY.items()},
        "segments": segments,
        "streams": {"time_s": pick(streams.time, nd=0), "distance_mi": pick(streams.distance, mi, 3),
                    "altitude_ft": pick(streams.altitude, ft, 0), "heart_rate": pick(hr, nd=0),
                    "zone": None if zone is None else [int(v) for v in zone[idx]],
                    "lat": pick(streams.lat, nd=6), "lon": pick(streams.lon, nd=6)},
    }


def _coach_settings(store: Store) -> dict:
    saved = store.settings()
    return {k: saved.get(k, v) for k, v in app_settings.defaults().items()}


def _is_owner() -> bool:
    """The owner (first account) has no server ceiling on coach spending. Outside a request,
    the CLI acts as the owner."""
    account = _account.get()
    return account is None or account.is_owner


def _coach_cap(store: Store) -> dict:
    """This calendar month's coach spending against the account's cap. Coach calls run on the
    server's Anthropic key, so every account other than the owner's is held under the server
    ceiling (TALUS_COACH_CAP_USD); each account can set its own cap lower."""
    ceiling = None if _is_owner() else app_settings.server_ceiling()
    chosen = store.settings().get("coach_monthly_cap_usd", ceiling)
    limit = chosen if ceiling is None else (ceiling if chosen is None else min(chosen, ceiling))
    month = date.today().replace(day=1)
    return {"limit": limit, "ceiling": ceiling, "spent": app_settings.spent_since(store, month),
            "month": month.isoformat()}


def _coach(store: Store, mode: str, subject: str, packet: dict, fallback: dict, refresh: bool) -> dict:
    """Reuse the logged reply for the same packet, model and prompt version, so reloading a page
    doesn't call the API again. Only API calls are logged; with no key or the coach off, the rules
    are shown as is."""
    cfg = _coach_settings(store)
    if not cfg["coach_enabled"]:
        return {"source": "rules", "reply": fallback, "log_id": None, "feedback": None,
                "fallback_reason": "off", "model": None}
    model_id, style = cfg[f"{mode}_model"], cfg["coach_style"]
    if style not in coach.STYLES:
        style = coach.DEFAULT_STYLE
    h = coach.cache_key(packet, model_id, style)
    row = None if refresh else store.coach_entry(mode, subject, coach.PROMPT_VERSION, h)
    if row is not None and not (row["fallback_reason"] or "").startswith("api_error"):
        return {"source": row["source"], "reply": json.loads(row["reply"]), "log_id": row["id"],
                "feedback": row["feedback"], "fallback_reason": row["fallback_reason"], "model": row["model"]}
    cap = _coach_cap(store)
    if cap["limit"] is not None and cap["spent"] >= cap["limit"]:
        return {"source": "rules", "reply": fallback, "log_id": None, "feedback": None,
                "fallback_reason": "cap", "model": None}
    result = coach.run(mode, packet, fallback, coach_client(), model_id, style)
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
    day = date.today()
    now = {d.ledger: _ledger_json(d) for d in store.ledgers(day) if d.day == day}
    packet = coach.analysis_packet(a, _thresholds(store), now, day)
    return _coach(store, "analysis", str(activity_id), packet, coach.rules_analysis(packet), refresh)


class Feedback(BaseModel):
    rating: int | None = Field(default=None, ge=-1, le=1)


@app.post("/api/coach/feedback/{log_id}")
def coach_feedback(log_id: int, body: Feedback) -> dict:
    if not get_store().coach_feedback(log_id, body.rating or None):
        raise HTTPException(404, "No such coach reply")
    return {"ok": True}


# Settings. Keys stay in .env: the engine reports whether each one is set, never its value.

INFER_DAYS = 180
_inferred_cache: dict = {}


def _inferred_thresholds(store: Store) -> dict | None:
    """Thresholds estimated from the last 180 days of runs (talus infer), with how far to trust
    each one and the runs it came from. Parsing every run is slow, so the result is kept until
    the set of activities changes."""
    from .infer import infer

    rows = [r for r in store.activities() if r["start_time"]]
    key = (str(store.dir), tuple(sorted(r["id"] for r in rows)), date.today())
    if key not in _inferred_cache:
        _inferred_cache.clear()
        acts, _ = cli.infer_inputs(store, INFER_DAYS)
        r = infer(acts)
        if not r["runs_with_hr"]:
            _inferred_cache[key] = None
        else:
            info = {row["id"]: {"id": row["id"], "day": row["start_time"][:10], "name": row["name"]} for row in rows}
            _inferred_cache[key] = {
                "days": INFER_DAYS, "runs_with_hr": r["runs_with_hr"],
                **{k: {"value": e.value, "confidence": e.confidence, "method": e.method,
                       "activities": [info[i] for i in e.activities if i in info]}
                   for k, e in ((k, r[k]) for k in ("aet", "ant", "max"))}}
    return _inferred_cache[key]


def _usage_estimate(store: Store, mode: str, model_id: str, calls_per_month: int) -> dict:
    avg_in, avg_out, n = store.coach_usage(mode)
    tokens = (avg_in, avg_out) if n else app_settings.TYPICAL_TOKENS[mode]
    per_call = app_settings.cost_per_call(model_id, tokens)
    return {"per_call": per_call, "per_month": None if per_call is None else per_call * calls_per_month,
            "calls_per_month": calls_per_month, "from_log": n > 0}


@app.get("/api/settings")
def get_settings() -> dict:
    store = get_store()
    th = store.thresholds()
    saved = store.settings()
    cfg = _coach_settings(store)
    acts = [r for r in store.activities() if r["start_time"]]
    month_ago = (date.today() - timedelta(days=30)).isoformat()
    analyses = sum(1 for r in acts if r["start_time"][:10] >= month_ago)
    cred = _intervals_credential()
    return {
        "thresholds": None if th is None else {
            "resting": th.resting, "aet": th.aet, "ant": th.ant, "max": th.max, "sex": th.sex,
            "threshold_pace_s_per_mi": th.threshold_pace_s_per_mi, "source": th.source,
            "aet_provisional": "provisional" in th.source, "zones": _zone_settings(store, th),
            "aerobic_gap": (th.ant - th.aet) / th.ant},
        "inferred": _inferred_thresholds(store),
        "source": {
            "name": "intervals.icu",
            "key_set": cred is not None,
            "kind": cred and cred["kind"],
            "athlete_id": cred and cred.get("athlete_id"),
            "updated_at": cred and cred["updated_at"],
            "garmin_fallback_set": _is_owner() and bool(os.environ.get("GARMIN_EMAIL")),
            "activities": len(acts),
            "latest_activity": max((r["start_time"][:10] for r in acts), default=None),
            "last_sync": saved.get("last_sync"),
        },
        "coach": {
            "enabled": cfg["coach_enabled"],
            "key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "brief_model": cfg["brief_model"],
            "analysis_model": cfg["analysis_model"],
            "style": cfg["coach_style"],
            "styles": [{"key": k, "label": v["label"], "description": v["description"]}
                       for k, v in coach.STYLES.items()],
            "models": [{"id": k, "label": v["label"], "input": v["input"], "output": v["output"]}
                       for k, v in app_settings.MODELS.items()],
            # Per model, so the screen can show what a switch would cost. Brief: about two a day,
            # after the sync and after the check-in. Analysis: one per activity, at the last 30 days' rate.
            "estimate": {mode: {m: _usage_estimate(store, mode, m, calls) for m in app_settings.MODELS}
                         for mode, calls in (("brief", 60), ("analysis", analyses))},
            "cap": _coach_cap(store),
        },
        "readiness": dict(zip(("sleep_need_h", "sleep_need_source"), wellness.sleep_need(saved))),
        "units": {"distance": "mi", "vert": "ft"},
    }


def _zone_settings(store: Store, th: Thresholds) -> dict:
    saved = store.settings().get("hr_zones")
    zm = zones.model(th, saved)
    custom = (saved or {}).get("custom") or {}
    return {**_zone_model_json(zm), "floors": zm["floors"], "derived": zm["derived"],
            "options": {str(n): {"derived": zones.derived_floors(th, n),
                                 "custom": custom.get(str(n)) if zones.valid_floors(custom.get(str(n)) or [], n) else None,
                                 "names": zones.NAMES[n]} for n in zones.NAMES}}


class ZonesIn(BaseModel):
    zones: int = Field(ge=3, le=5)
    floors: list[int] | None = None  # None keeps any earlier edits for this model
    reset: bool = False  # back to the floors derived from the thresholds


@app.put("/api/settings/zones")
def put_zones(body: ZonesIn) -> dict:
    """Pick 3 or 5 zones and optionally set each zone's starting bpm. Loads don't change."""
    if body.zones not in zones.NAMES:
        raise HTTPException(422, "Choose 3 or 5 zones.")
    if body.floors is not None and not zones.valid_floors(body.floors, body.zones):
        raise HTTPException(422, f"Give {body.zones - 1} zone starts in bpm, each higher than the one before.")
    store = get_store()
    th = store.thresholds()
    if th is None:
        raise HTTPException(409, "Set heart rate thresholds first.")
    saved = store.settings().get("hr_zones") or {}
    custom = dict(saved.get("custom") or {})
    if body.reset or body.floors == zones.derived_floors(th, body.zones):
        custom.pop(str(body.zones), None)
    elif body.floors is not None:
        custom[str(body.zones)] = body.floors
    store.save_settings({"hr_zones": {"zones": body.zones, "custom": custom}})
    # Activities scored before the heart-rate histogram existed need one rescore to show any model.
    rescored = 0
    if any(r["summary"] and "hr_hist" not in json.loads(r["summary"]) for r in store.activities()):
        rescored = cli.rescore(store, th, log=lambda _: None)
    return {"ok": True, "rescored": rescored, "zones": _zone_settings(store, th)}


class ThresholdsIn(BaseModel):
    resting: float = Field(ge=25, le=120)
    aet: float | None = Field(default=None, ge=60, le=230)
    ant: float = Field(ge=80, le=240)
    max: float = Field(ge=100, le=250)
    sex: str = Field(default="male", pattern="^(male|female)$")
    threshold_pace_s_per_mi: float | None = Field(default=None, ge=180, le=1800)


@app.put("/api/settings/thresholds")
def put_thresholds(body: ThresholdsIn) -> dict:
    """Same rules as `talus thresholds`: no AeT means 88% of AnT, marked provisional. Every stored
    activity is rescored, since zones and cardio load depend on these numbers."""
    aet = body.aet if body.aet is not None else round(body.ant * 0.88)
    if not body.resting < aet < body.ant <= body.max:
        raise HTTPException(422, "Heart rates must rise in order: resting, then AeT, then AnT, then max.")
    th = Thresholds(body.resting, aet, body.ant, body.max, body.sex, body.threshold_pace_s_per_mi,
                    "manual" if body.aet is not None else "manual, AeT provisional (88% of AnT)")
    store = get_store()
    store.set_thresholds(th)
    n = cli.rescore(store, th, log=lambda _: None)
    return {"ok": True, "rescored": n}


class CoachIn(BaseModel):
    enabled: bool
    brief_model: str
    analysis_model: str
    monthly_cap_usd: float | None = Field(default=None, ge=0, le=1000)
    style: str | None = None  # left out keeps the saved style


@app.put("/api/settings/coach")
def put_coach(body: CoachIn) -> dict:
    for m in (body.brief_model, body.analysis_model):
        if m not in app_settings.MODELS:
            raise HTTPException(422, f"Unknown model {m}")
    if body.style is not None and body.style not in coach.STYLES:
        raise HTTPException(422, f"Unknown style {body.style}")
    values = {"coach_enabled": body.enabled, "brief_model": body.brief_model,
              "analysis_model": body.analysis_model}
    if body.style is not None:
        values["coach_style"] = body.style
    if "monthly_cap_usd" in body.model_fields_set:
        values["coach_monthly_cap_usd"] = body.monthly_cap_usd
    get_store().save_settings(values)
    return {"ok": True}


class ReadinessIn(BaseModel):
    sleep_need_h: float = Field(ge=4, le=12)


@app.put("/api/settings/readiness")
def put_readiness(body: ReadinessIn) -> dict:
    """Sleep need, which readiness reads last night and the 7-day average against."""
    get_store().save_settings({"sleep_need_h": round(body.sleep_need_h * 4) / 4})
    return {"ok": True}


def _intervals_credential() -> dict | None:
    return cli.credential_for(get_accounts(), _account.get())


class SourceIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)
    athlete_id: str = Field(default="0", pattern=r"^(0|i?\d{1,12})$")


@app.put("/api/settings/source")
def put_source(body: SourceIn) -> dict:
    """Save this account's intervals.icu key. It is encrypted at rest and never sent back."""
    get_accounts().set_credential(current_account().id, "intervals", "api_key",
                                  {"api_key": body.api_key.strip(), "athlete_id": body.athlete_id})
    return {"ok": True}


@app.delete("/api/settings/source")
def delete_source() -> dict:
    get_accounts().clear_credential(current_account().id, "intervals")
    return {"ok": True}


class SyncIn(BaseModel):
    days: int = Field(default=30, ge=1, le=730)


@app.post("/api/sync")
def sync_now(body: SyncIn, background: BackgroundTasks) -> dict:
    store = get_store()
    cred = _intervals_credential()
    if cred is None:
        raise HTTPException(409, "Add your intervals.icu API key under Data source first.")
    lines: list[str] = []
    try:
        result = cli.sync(store, "intervals", date.today() - timedelta(days=body.days), log=lines.append,
                          credential=cred)
    except Exception:
        return store.settings()["last_sync"] | {"log": lines}
    # The coach writes after the response, so the sync button doesn't wait on it.
    background.add_task(warm_coach, result.pop("added_ids"))
    return result | {"log": lines}


# The analysis is written right after a sync only for recent activities: a first sync of a
# year of history shouldn't pay for a read of every old run. Older ones get theirs when opened.
WARM_ANALYSIS_DAYS = 14


def warm_coach(activity_ids: list[int]) -> list[str]:
    """Write today's brief and each new recent activity's read right after a sync, so the pages
    open with them ready. Replies are cached as usual, so opening a page doesn't pay again."""
    try:
        b = coach_brief()
    except HTTPException as e:
        return [f"Coach skipped: {e.detail}"]
    if b["fallback_reason"] in ("no_key", "off"):
        return ["Coach skipped: " + ("no ANTHROPIC_API_KEY" if b["fallback_reason"] == "no_key" else "turned off in Settings")]
    lines = [f"Coach brief: {b['reply']['call']} ({b['source']})"]
    store = get_store()
    recent = (date.today() - timedelta(days=WARM_ANALYSIS_DAYS)).isoformat()
    for aid in dict.fromkeys(activity_ids):
        row = store.activity(aid)
        if row is None or (row["start_time"] or "") < recent:
            continue
        try:
            r = coach_activity(aid)
        except (HTTPException, ValueError):  # unreadable file: the activity page says why
            continue
        lines.append(f"Coach read for activity {aid}: {r['source']}")
    return lines


# Tap-a-number explanations

@app.get("/api/explain/ledger/{name}/{metric}")
def explain_ledger(name: str, metric: str, day: date | None = None) -> dict:
    if name not in ledger.LEDGERS or metric not in explain.LEDGER_METRICS:
        raise HTTPException(404, "Unknown ledger or number")
    store = get_store()
    day = day or date.today()
    rows = {(d.day, d.ledger): d for d in store.ledgers(day - timedelta(days=1)) if d.day <= day}
    now = rows.get((day, name))
    if now is None:
        raise HTTPException(404, "No ledger for that day")
    acts = [(a["name"] or a["sport"] or "Activity", a[ledger.ACTIVITY_KEY[name]])
            for a in _activities(store, day) if a["day"] == day.isoformat()]
    return explain.ledger_metric(name, metric, now, rows.get((day - timedelta(days=1), name)), acts)


@app.get("/api/explain/activity/{activity_id}/{metric}")
def explain_activity(activity_id: int, metric: str) -> dict:
    if metric not in explain.ACTIVITY_METRICS:
        raise HTTPException(404, "Unknown number")
    store = get_store()
    row = store.activity(activity_id)
    th = store.thresholds()
    if row is None or th is None:
        raise HTTPException(404, "No such activity, or thresholds not set")
    streams = parse(store.raw(row))
    streams.sport = streams.sport or row["sport"]
    r = score(streams, th)
    if metric.startswith("times_usual_"):
        name = metric.removeprefix("times_usual_")
        day = date.fromisoformat(row["start_time"][:10])
        before = {d.ledger: d.fitness for d in store.ledgers(day - timedelta(days=1))
                  if d.day == day - timedelta(days=1)}
        return explain.times_usual(name, getattr(r, ledger.ACTIVITY_KEY[name]), before.get(name))
    if metric == "cardio_load":
        dt = np.minimum(np.diff(streams.time, prepend=streams.time[0]), MAX_DT_S)
        return explain.cardio_load(r, streams, dt, th)
    return getattr(explain, metric)(r)


# Accounts: sign up, sign in, sign out, password, export and delete.

class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
MAX_FAILURES = 10
FAILURE_WINDOW_S = 15 * 60


def _too_many_failures(email: str, failed: bool = False) -> bool:
    key = email.strip().lower()
    now = time.monotonic()
    with _failures_lock:
        recent = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW_S]
        if failed:
            recent.append(now)
        _failures[key] = recent
        return len(recent) >= MAX_FAILURES


def _signup_open(accounts: Accounts) -> bool:
    """Anyone who can reach the app can make an account, unless TALUS_SIGNUP=closed. The first
    account can always be made, so a fresh or upgraded install can be claimed."""
    return accounts.count() == 0 or os.environ.get("TALUS_SIGNUP", "open").lower() != "closed"


def _signed_in(response: Response, accounts: Accounts, account: Account) -> dict:
    response.set_cookie(COOKIE, accounts.start_session(account.id), max_age=SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", path="/",
                        secure=os.environ.get("TALUS_SECURE_COOKIE", "") == "1")
    return _account_json(account)


def _account_json(account: Account) -> dict:
    return {"id": account.id, "email": account.email, "is_owner": account.is_owner,
            "created_at": account.created_at}


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    accounts = get_accounts()
    account = accounts.session_account(request.cookies.get(COOKIE))
    return {"account": account and _account_json(account), "has_accounts": accounts.count() > 0,
            "signup_open": _signup_open(accounts), "existing_data": accounts.legacy_data_waiting()}


@app.post("/api/auth/signup")
def signup(body: Credentials, response: Response) -> dict:
    accounts = get_accounts()
    if not _signup_open(accounts):
        raise HTTPException(403, "New accounts are closed on this Talus. Ask its owner.")
    try:
        account = accounts.create(body.email, body.password)
    except AccountError as e:
        raise HTTPException(422, str(e))
    return _signed_in(response, accounts, account)


@app.post("/api/auth/login")
def login(body: Credentials, response: Response) -> dict:
    if _too_many_failures(body.email):
        raise HTTPException(429, "Too many tries. Wait 15 minutes, then try again.")
    accounts = get_accounts()
    account = accounts.authenticate(body.email, body.password)
    if account is None:
        _too_many_failures(body.email, failed=True)
        raise HTTPException(401, "That email and password don't match an account.")
    return _signed_in(response, accounts, account)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    get_accounts().end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/account")
def get_account() -> dict:
    return _account_json(current_account())


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@app.put("/api/account/password")
def change_password(body: PasswordIn, response: Response) -> dict:
    """Changing the password signs out every session, this one included, then signs this one back in."""
    accounts = get_accounts()
    account = current_account()
    if accounts.authenticate(account.email, body.current) is None:
        raise HTTPException(403, "Your current password is wrong.")
    try:
        accounts.set_password(account.id, body.new)
    except AccountError as e:
        raise HTTPException(422, str(e))
    return _signed_in(response, accounts, account)


@app.get("/api/account/export")
def export_account() -> StreamingResponse:
    """Everything Talus holds for this account as one zip: a consistent copy of its database
    (activities, scores, ledgers, check-ins, wellness, settings, coach log) and every raw file.
    Sign-in details and data-source keys are left out."""
    account = current_account()
    folder = get_accounts().user_dir(account.id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if (folder / "talus.db").exists():
            snap = sqlite3.connect(":memory:")
            src = sqlite3.connect(folder / "talus.db")
            src.backup(snap)
            src.close()
            z.writestr("talus.db", snap.serialize())
            snap.close()
        for path in sorted((folder / "raw").rglob("*")) if (folder / "raw").exists() else []:
            if path.is_file():
                z.write(path, str(path.relative_to(folder)))
        z.writestr("account.json", json.dumps({**_account_json(account),
                                               "exported_at": datetime.now().isoformat(timespec="seconds")},
                                              indent=2))
    buf.seek(0)
    name = f"talus-export-{date.today().isoformat()}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


class DeleteIn(BaseModel):
    password: str = Field(max_length=256)


@app.post("/api/account/delete")
def delete_account(body: DeleteIn, response: Response) -> dict:
    """Delete the account and all its data. Needs the password again; cannot be undone."""
    accounts = get_accounts()
    account = current_account()
    if accounts.authenticate(account.email, body.password) is None:
        raise HTTPException(403, "Your password is wrong.")
    accounts.delete(account.id)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
