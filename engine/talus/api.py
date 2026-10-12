"""HTTP API over the engine for the web app. Run: uv run talus serve

Every number here comes from the engine; the web app only formats it. All distances are
miles and all vert is feet.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import threading
import time
import zipfile
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Literal

import httpx
import numpy as np
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import MODEL_VERSION, admin, cli, coach, explain, google_auth, interview, intervals_auth, ledger, readiness_score, wellness, zones
from . import plan as planner
from . import schedule
from . import settings as app_settings
from .accounts import SESSION_DAYS, Account, AccountError, Accounts
from .config import data_dir, load_env
from .models import Thresholds
from .parse import parse
from .readiness import daily_call, readiness
from .route import route_numbers
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


# Each scored activity's summary, parsed, with its time in each display zone. Every page reads the
# activity list, often several times per request, so the parse is kept per summary and zone floors.
_summaries: OrderedDict[tuple, tuple[dict, dict | None]] = OrderedDict()
_summaries_guard = threading.Lock()
SUMMARIES_KEEP = 5000


def _summary(text: str, zm: dict | None) -> tuple[dict, dict | None]:
    key = (text, None if zm is None else (zm["zones"], zm["custom"], tuple(zm["floors"])))
    with _summaries_guard:
        hit = _summaries.get(key)
        if hit is not None:
            _summaries.move_to_end(key)
            return hit
    m = json.loads(text)
    hit = (m, _display_minutes(m, zm))
    with _summaries_guard:
        _summaries[key] = hit
        while len(_summaries) > SUMMARIES_KEEP:
            _summaries.popitem(last=False)
    return hit


def _activities(store: Store, since: date | None = None, limit: int | None = None) -> list[dict]:
    zm = _zone_model(store)
    out = []
    for row in store.scored_activities(since and since.isoformat(), limit):
        m, hr_zone_minutes = _summary(row["summary"], zm)
        out.append({"id": row["id"], "day": row["start_time"][:10], "start_time": row["start_time"],
                    "name": row["name"], "sport": row["sport"], "source": row["source"],
                    "device_name": row["device_name"], **{k: m[k] for k in (
                        "distance_mi", "gain_ft", "loss_ft", "effort_mi", "moving_time_s", "elapsed_time_s",
                        "cardio_load", "climb_load", "descent_load", "cardio_estimated", "zone_minutes",
                        "hike_share", "notes")}, "hr_zone_minutes": hr_zone_minutes,
                    "longest_descent_ft": m.get("longest_descent_ft"),
                    "minutes_above_8000ft": m.get("minutes_above_8000ft")})
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
    need = wellness.sleep_need(store.settings())[0]
    nights = store.wellness(day - timedelta(days=wellness.BASELINE_DAYS + TREND_DAYS - 2), day)
    overnight = wellness.evaluate(nights, day, need)
    ready = readiness(now, checkin, overnight)
    week_start = day - timedelta(days=day.weekday())
    warm_details(store)  # the activities Today links to open already scored
    week = sorted(_activities(store, week_start), key=lambda a: a["start_time"])
    last_28 = _activities(store, day - timedelta(days=27))
    zm = _zone_model(store)
    call = daily_call(ready, checkin, th.aet) | {"source": "rules", "planned": False}
    planned = _planned_today(store, day, th.aet)
    if planned and call["call"] == "go":
        # A scheduled run sets a "go" day's session; a modify or rest call keeps its easier one,
        # so the call can ease a planned run but never make it harder.
        call |= {"session": planned["title"], "detail": planned["detail"], "planned": True}
    return {
        "date": day.isoformat(),
        "planned": planned,
        "checkin": checkin,
        "wellness": overnight,
        "readiness": ready,
        "score": readiness_score.score(now, ready, checkin, overnight, th.aet)
                 | {"trend": _score_trend(store, day, recent, nights, need, th.aet)},
        "call": call,
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


TREND_DAYS = 28


def _score_trend(store: Store, day: date, recent: list[ledger.LedgerDay], nights: dict, need: float,
                 aet: float) -> list[dict]:
    """The readiness score for each of the last TREND_DAYS days, from what was known that morning.
    Days before the current run of data have no score."""
    start = day - timedelta(days=TREND_DAYS - 1)
    by_day: dict[date, dict[str, ledger.LedgerDay]] = {}
    for d in recent:
        if d.day >= start:
            by_day.setdefault(d.day, {})[d.ledger] = d
    checkins = store.checkins(start, day)
    out = []
    for i in range(TREND_DAYS):
        d = start + timedelta(days=i)
        if d not in by_day:
            continue
        w = wellness.evaluate(nights, d, need)
        s = readiness_score.score(by_day[d], readiness(by_day[d], checkins.get(d), w), checkins.get(d), w, aet)
        out.append({"day": d.isoformat(), "score": s["score"], "label": s["label"], "call": s["call"]})
    return out


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
    this_week = day - timedelta(days=day.weekday())
    last_week = this_week - timedelta(weeks=1)
    return {
        "date": day.isoformat(),
        "ledgers": {name: {"today": _ledger_json([d for d in series if d.ledger == name][-1], hist["since"])
                           if series else None,
                           "series": [_ledger_json(d, hist["since"]) for d in series if d.ledger == name]}
                    for name in ledger.LEDGERS},
        "history": {"since": hist["since"] and hist["since"].isoformat(), "gap": hist["gap"]},
        "intensity_by_week": weeks,
        "weekly": [_week_totals(acts, w["start"], (date.fromisoformat(w["start"]) + timedelta(days=7)).isoformat())
                   | {"days": min(7, (day - date.fromisoformat(w["start"])).days + 1)} for w in weeks],
        # Last week through the same weekday as today, so the week in progress compares like for like.
        "last_week_to_date": _week_totals(acts, last_week.isoformat(),
                                          (last_week + timedelta(days=day.weekday() + 1)).isoformat()),
        "zone_model": _zone_model_json(zm),
        "biggest_days": sorted(acts, key=lambda a: a["effort_mi"], reverse=True)[:5],
        "thresholds": _thresholds(store),
        "projection": _projection(store, day),
    }


def _week_totals(acts: list[dict], start: str, end: str) -> dict:
    """What was done from `start` up to, not including, `end`: every activity, as the Activities page counts it."""
    got = [a for a in acts if start <= a["day"] < end]
    return {"start": start, "activities": len(got),
            **{k: sum(a[k] or 0 for a in got) for k in (
                "distance_mi", "gain_ft", "loss_ft", "effort_mi", "moving_time_s",
                "cardio_load", "climb_load", "descent_load")}}


def _projection(store: Store, day: date) -> dict:
    """Where the scheduled runs take each ledger, drawn after today on the Load charts."""
    acts = _activities(store, day - timedelta(days=planner.CAPACITY_DAYS))
    items = _calendar(store, day, schedule.PROJECT_DAYS + 1, acts)
    done_today = any(a["day"] == day.isoformat() and planner.is_foot(a["sport"]) for a in acts)
    return schedule.project(items, _plan_history(store, day), planner.rates(acts, day), day, done_today)


@app.get("/api/activities")
def activities(limit: int = 50) -> list[dict]:
    store = get_store()
    warm_details(store)
    return _activities(store, limit=limit)


# Recently read activity files. Reading a FIT file takes about a second, and an activity page
# reads it twice (the page, then the coach's read), so keep the last few parsed.
_parsed: OrderedDict[tuple, object] = OrderedDict()
_parsed_guard = threading.Lock()
PARSED_KEEP = 8


def _streams(store: Store, row):
    """An activity's parsed file, or a 422 that says why it can't be read."""
    path = store.dir / row["raw_path"]
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size, row["raw_fmt"])
        with _parsed_guard:
            streams = _parsed.get(key)
            if streams is not None:
                _parsed.move_to_end(key)
        if streams is None:
            streams = parse(store.raw(row), check_crc=False)
            with _parsed_guard:
                _parsed[key] = streams
                while len(_parsed) > PARSED_KEEP:
                    _parsed.popitem(last=False)
    except Exception as e:
        raise HTTPException(422, f"Could not read this activity's file ({type(e).__name__}: {e}).") from e
    streams.sport = streams.sport or row["sport"]
    return streams


# What an activity page shows from its file: scoring, segments and the drawn streams. Reading a
# FIT file and scoring it takes about a second on the server, so the result is kept on disk next
# to the account's data (and the last few in memory), keyed on everything it depends on: the file,
# thresholds, zone floors and the scoring model. Loads against the athlete's usual day are cheap
# and change daily, so they are worked out on every request.
_details: OrderedDict[tuple, dict] = OrderedDict()
_details_guard = threading.Lock()
DETAILS_KEEP = 32
# One reader per activity: the page and the coach's read ask for it at the same moment, and the
# second waits for the first instead of reading the file again.
_detail_locks: dict[tuple, threading.Lock] = {}


def _detail_key(store: Store, row, th: Thresholds, zm: dict) -> str:
    st = (store.dir / row["raw_path"]).stat()
    return json.dumps({"file": [row["raw_path"], st.st_mtime_ns, st.st_size, row["raw_fmt"], row["sport"]],
                       "th": asdict(th), "zones": [zm["zones"], zm["floors"]], "model": MODEL_VERSION,
                       "points": MAX_POINTS}, sort_keys=True)


def _detail_core(store: Store, row, th: Thresholds, zm: dict) -> dict:
    try:
        key = _detail_key(store, row, th, zm)
    except OSError as e:
        raise HTTPException(422, f"Could not read this activity's file ({type(e).__name__}: {e}).") from e
    mem_key = (str(store.dir), row["id"], key)
    with _details_guard:
        hit = _details.get(mem_key)
        if hit is not None:
            _details.move_to_end(mem_key)
            return hit
    with _details_guard:
        lock = _detail_locks.setdefault(mem_key[:2], threading.Lock())
    with lock:
        return _detail_core_locked(store, row, th, zm, key, mem_key)


def _detail_core_locked(store: Store, row, th: Thresholds, zm: dict, key: str, mem_key: tuple) -> dict:
    with _details_guard:
        hit = _details.get(mem_key)
    if hit is not None:
        return hit
    path = store.dir / "cache" / f"activity-{row['id']}.json"
    try:
        saved = json.loads(path.read_text())
        if saved.get("key") == key:
            hit = saved["detail"]
    except (OSError, ValueError, KeyError):
        pass
    if hit is None:
        hit = _score_detail(store, row, th, zm)
        try:
            path.parent.mkdir(exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"key": key, "detail": hit}))
            tmp.replace(path)
        except (OSError, TypeError):
            pass  # a read-only or full disk only costs the next view a re-read
    with _details_guard:
        _details[mem_key] = hit
        while len(_details) > DETAILS_KEEP:
            _details.popitem(last=False)
    return hit


def _score_detail(store: Store, row, th: Thresholds, zm: dict) -> dict:
    streams = _streams(store, row)
    result = score(streams, th)
    hr = streams.heart_rate
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
        "sport": streams.sport,
        "distance_mi": result.distance_mi, "gain_ft": result.gain_ft, "loss_ft": result.loss_ft,
        "effort_mi": result.effort_mi, "moving_time_s": result.moving_time_s,
        "elapsed_time_s": result.elapsed_time_s, "hike_share": result.hike_share,
        "zone_minutes": result.zone_minutes, "zone_model": _zone_model_json(zm),
        "hr_zone_minutes": zones.minutes(result.hr_hist, zm["floors"]) if hr is not None else None,
        "gain_by_band_ft": result.gain_by_band_ft,
        "loss_by_band_ft": result.loss_by_band_ft, "longest_climb_ft": result.longest_climb_ft,
        "longest_descent_ft": result.longest_descent_ft, "minutes_above_8000ft": result.minutes_above_8000ft,
        "cardio_estimated": result.cardio_estimated, "notes": result.notes,
        "load_values": {name: getattr(result, key) for name, key in ledger.ACTIVITY_KEY.items()},
        "segments": segments,
        "streams": {"time_s": pick(streams.time, nd=0), "distance_mi": pick(streams.distance, mi, 3),
                    "altitude_ft": pick(streams.altitude, ft, 0), "heart_rate": pick(hr, nd=0),
                    "zone": None if zone is None else [int(v) for v in zone[idx]],
                    "lat": pick(streams.lat, nd=6), "lon": pick(streams.lon, nd=6)},
    }


# Folders whose newest activities are being read ahead, by a background thread each.
_warming: set[str] = set()
_warming_guard = threading.Lock()
WARM_DETAILS = 10


def warm_details(store: Store, ids: list[int] | None = None) -> None:
    """Read the newest activities' files (or `ids`) ahead in the background, so opening one finds
    it already scored. Each is read once; later warms find it on disk and skip it."""
    key = str(store.dir)
    th = store.thresholds()
    if th is None:
        return
    zm = zones.model(th, store.settings().get("hr_zones"))
    rows = [store.activity(i) for i in ids] if ids is not None else store.scored_activities(limit=WARM_DETAILS)
    rows = [r for r in rows if r is not None][:WARM_DETAILS]

    def run() -> None:
        try:
            for row in rows:
                try:
                    _detail_core(store, row, th, zm)
                except Exception:  # an unreadable file: its page says why when opened
                    pass
        finally:
            with _warming_guard:
                _warming.discard(key)

    with _warming_guard:
        if key in _warming:
            return
        _warming.add(key)
    threading.Thread(target=run, name=f"warm {key}", daemon=True).start()


@app.get("/api/activities/{activity_id}")
def activity(activity_id: int) -> dict:
    store = get_store()
    row = store.activity(activity_id)
    th = store.thresholds()
    if row is None or th is None:
        raise HTTPException(404, "No such activity, or thresholds not set")
    zm = zones.model(th, store.settings().get("hr_zones"))
    core = dict(_detail_core(store, row, th, zm))
    load_values = core.pop("load_values")
    day = date.fromisoformat(row["start_time"][:10])
    before = {d.ledger: d.fitness for d in store.ledgers(day - timedelta(days=1))
              if d.day == day - timedelta(days=1)}
    six_weeks = [a for a in _activities(store, day - timedelta(days=42)) if a["day"] <= day.isoformat()]

    def usual(name: str, value: float | None) -> dict:
        others = [a[ledger.ACTIVITY_KEY[name]] or 0 for a in six_weeks if a["id"] != activity_id]
        return {"load": value,
                "times_usual_day": None if value is None else ledger.times_usual_day(value, before.get(name, 0.0)),
                "six_week_high": value is not None and bool(others) and value > max(others)}

    return {"id": activity_id, "name": row["name"], "day": day.isoformat(), "start_time": row["start_time"],
            "source": row["source"], "device_name": row["device_name"], **core,
            "loads": {name: usual(name, load_values[name]) for name in ledger.ACTIVITY_KEY}}


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
    account = _account.get()
    ceiling = None if account is None else admin.coach_ceiling(account)
    chosen = store.settings().get("coach_monthly_cap_usd", ceiling)
    limit = chosen if ceiling is None else (ceiling if chosen is None else min(chosen, ceiling))
    month = date.today().replace(day=1)
    return {"limit": limit, "ceiling": ceiling, "spent": app_settings.spent_since(store, month),
            "month": month.isoformat()}


def _show_ai_cost() -> bool:
    """The admin page's "Show AI cost" feature flag, for every account. Off by default."""
    return bool(get_accounts().config("show_ai_cost"))


def _shown_cost(model: str | None, usage, cached: bool) -> dict | None:
    """The cost line under a coach reply, when the flag is on. A reused reply cost nothing this
    time; it carries what its first call cost."""
    if not _show_ai_cost():
        return None
    cost = app_settings.call_cost(model, usage)
    return None if cost is None else cost | {"cached": cached}


def _chat_costs(messages: list[dict]) -> list[dict]:
    """Each coach answer's cost from its logged call, when the flag is on. An answer reusing an
    earlier logged call shows as cached."""
    if not _show_ai_cost():
        return messages
    store, seen = get_store(), set()
    for m in messages:
        m["cost"] = None
        if m["role"] == "coach" and m["log_id"]:
            r = store.db.execute("SELECT model, usage FROM coach_log WHERE id = ?", (m["log_id"],)).fetchone()
            cost = r and app_settings.call_cost(r["model"], r["usage"])
            m["cost"] = cost and cost | {"cached": m["log_id"] in seen}
            seen.add(m["log_id"])
    return messages


def _coach(store: Store, mode: str, subject: str, packet: dict, fallback: dict, refresh: bool) -> dict:
    """Reuse the logged reply for the same packet, model and prompt version, so reloading a page
    doesn't call the API again. Only API calls are logged; with no key or the coach off, the rules
    are shown as is."""
    cfg = _coach_settings(store)
    if not cfg["coach_enabled"]:
        return {"source": "rules", "reply": fallback, "log_id": None, "feedback": None,
                "fallback_reason": "off", "model": None, "cost": None}
    # Plan, weekly review and chat are longer reads, so they run on the analysis model; reading
    # typed answers into fields is short work for the brief's cheaper model.
    model_id = cfg["brief_model"] if mode in coach.FIELD_MODES else cfg.get(f"{mode}_model", cfg["analysis_model"])
    style = cfg["coach_style"]
    if style not in coach.STYLES:
        style = coach.DEFAULT_STYLE
    h = coach.cache_key(packet, model_id, style)
    row = None if refresh else store.coach_entry(mode, subject, coach.PROMPT_VERSION, h)
    if row is not None and not (row["fallback_reason"] or "").startswith("api_error"):
        return {"source": row["source"], "reply": json.loads(row["reply"]), "log_id": row["id"],
                "feedback": row["feedback"], "fallback_reason": row["fallback_reason"], "model": row["model"],
                "cost": _shown_cost(row["model"], row["usage"], cached=True)}
    cap = _coach_cap(store)
    if cap["limit"] is not None and cap["spent"] >= cap["limit"]:
        return {"source": "rules", "reply": fallback, "log_id": None, "feedback": None,
                "fallback_reason": "cap", "model": None, "cost": None}
    result = coach.run(mode, packet, fallback, coach_client(), model_id, style)
    log_id = None
    if result.fallback_reason != "no_key":
        log_id = store.log_coach(mode, subject, coach.PROMPT_VERSION, h, packet, result)
    return {"source": result.source, "reply": result.reply, "log_id": log_id, "feedback": None,
            "fallback_reason": result.fallback_reason, "model": result.model,
            "cost": _shown_cost(result.model, result.usage, cached=False)}


@app.get("/api/coach/brief")
def coach_brief(refresh: bool = False) -> dict:
    t = today()
    store = get_store()
    day = date.fromisoformat(t["date"])
    acts = _activities(store, day - timedelta(weeks=coach.BRIEF_WEEKS))
    packet = coach.brief_packet(t, [a for a in acts if a["day"] >= (day - timedelta(days=13)).isoformat()], acts)
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
    since = date.fromisoformat(a["day"]) - timedelta(days=max(coach.SIMILAR_DAYS, coach.BEST_DAYS))
    packet = coach.analysis_packet(a, _thresholds(store), now, day, _activities(store, since))
    return _coach(store, "analysis", str(activity_id), packet, coach.rules_analysis(packet), refresh)


class Feedback(BaseModel):
    rating: int | None = Field(default=None, ge=-1, le=1)


@app.post("/api/coach/feedback/{log_id}")
def coach_feedback(log_id: int, body: Feedback) -> dict:
    if not get_store().coach_feedback(log_id, body.rating or None):
        raise HTTPException(404, "No such coach reply")
    return {"ok": True}


# Objective, plan, weekly review and chat. Every number is the engine's; the coach explains.

def _plan_history(store: Store, day: date) -> list[ledger.LedgerDay]:
    return ledger.current_span(store.ledgers(day - timedelta(days=ledger.FITNESS_DAYS * 2)))


def _descent_fitness(history: list[ledger.LedgerDay]) -> float:
    return next((d.fitness for d in reversed(history) if d.ledger == "descent"), 0.0)


def _constraints(store: Store) -> planner.Constraints:
    return planner.Constraints.from_json(store.settings().get("plan_constraints"))


def _readiness_json(r: dict) -> dict:
    return {k: r[k] for k in ("dims", "limiter", "verdict")}


def _own(store: Store, day: date) -> dict[str, dict]:
    """The athlete's own runs in the weeks the plan's dated sessions touch."""
    return schedule.own_by_day(store.planned_runs(day - timedelta(days=day.weekday()),
                                                  day + timedelta(days=schedule.plan_days(day))))


def _sessions(store: Store, accepted: dict | None, acts: list[dict], day: date, aet: float, days: int = 7) -> list[dict]:
    if accepted is None:
        return []
    name = accepted["objective"]["name"]
    th = store.thresholds()
    return [s | planner.session_text(s, aet, name, th.ant if th else None)
            for s in planner.upcoming_sessions(accepted, acts, day, days, own=_own(store, day))]


def _zone_text(zm: dict | None) -> dict[int, str]:
    return {} if zm is None else {z["zone"]: f"zone {z['zone']}, {z['text']}" for z in zm["list"]}


def _calendar(store: Store, start: date, days: int, acts: list[dict] | None = None) -> list[dict]:
    day = date.today()
    acts = _activities(store, min(start, day) - timedelta(days=planner.CAPACITY_DAYS)) if acts is None else acts
    th = store.thresholds()
    return schedule.calendar(store.planned_runs(start, start + timedelta(days=days - 1)), store.plan("accepted"),
                             acts, start, days, day, planner.pace(acts, day)["min_per_effort_mi"],
                             th.aet if th else None, _zone_text(_zone_model(store)), own=_own(store, day),
                             ant=th.ant if th else None)


def _planned_today(store: Store, day: date, aet: float) -> dict | None:
    """Today's scheduled run, the athlete's own or the plan's, unless it's already done."""
    acts = _activities(store, day - timedelta(days=planner.CAPACITY_DAYS))
    items = [it for it in _calendar(store, day, 1, acts) if it["status"] == "today"]
    if not items:
        # A plan rest day is still the plan: on a go day the call follows it.
        rest = [s for s in _sessions(store, store.plan("accepted"), acts, day, aet, 1) if s["kind"] == "rest"]
        return None if not rest else {"title": "Rest", "detail": "Your plan has no run today. Rest, or an easy walk.",
                                      "source": "plan", "kind": "rest", "miles": 0.0, "gain_ft": 0.0, "loss_ft": 0.0}
    it = items[0]
    return {"title": it["title"], "detail": it["detail"], "source": it["source"], "kind": it["kind"],
            "miles": it["miles"], "gain_ft": it["gain_ft"], "loss_ft": it["loss_ft"]}


def _build_draft(store: Store, obj: planner.Objective, reason: str) -> dict:
    day = date.today()
    acts = _activities(store)
    history = _plan_history(store, day)
    c = _constraints(store)
    if planner.baseline(acts, day)["miles"] < 1:
        raise HTTPException(409, "Talus builds the plan from your last four weeks of training, and there's none in "
                                 "them yet. Sync your recent activities first.")
    try:
        body = planner.build(obj, acts, history, c, day)
        body["options"] = planner.options(obj, acts, history, c, day, body)
    except planner.PlanError as e:
        raise HTTPException(422, str(e))
    body["now"] = _readiness_json(planner.readiness_now(obj, acts, _descent_fitness(history), day))
    body["changes"] = planner.diff(store.plan("accepted"), body)
    store.save_draft(obj.id, reason, body)
    return body


def _last_week_review(store: Store, accepted: dict | None, acts: list[dict], day: date) -> dict:
    start = day - timedelta(days=day.weekday() + 7)
    review = planner.weekly_review(start, acts, accepted, store.ledgers(start - timedelta(days=1), day),
                                   store.pains(start, day))
    # Today's state counts too: a flag raised since Sunday, or pain reported this week.
    now = [d for d in store.ledgers(day) if d.day == day and d.ledger in planner.PLAN_LEDGERS]
    if "flag" not in review["replan"] and any(f in ("ramp", "overreach") for d in now for f in d.flags):
        review["replan"].append("flag")
    if accepted is None:
        review["replan"] = []
    review["replan_text"] = [planner.REPLAN_TEXT[r] for r in review["replan"]]
    return review


def _plan_json(p: dict | None) -> dict | None:
    if p is None:
        return None
    return {k: p.get(k) for k in ("id", "status", "reason", "created_at", "decided_at", "built_on", "constraints",
                                  "baseline", "pace", "demand", "peak", "weeks", "projected", "reachable",
                                  "options", "now", "changes")} | {"reason_text": planner.REPLAN_TEXT.get(p["reason"], p["reason"])}


def _with_own_runs(store: Store, sessions: list[dict], acts: list[dict]) -> list[dict]:
    """The plan's next 7 days, with any run the athlete scheduled on a day in place of the plan's."""
    if not sessions:
        return sessions
    start = date.fromisoformat(sessions[0]["day"])
    own = {it["day"]: it for it in _calendar(store, start, len(sessions), acts) if it["source"] == "manual"}
    out = []
    for s in sessions:
        it = own.get(s["day"])
        out.append(s if it is None else s | {k: it[k] for k in ("kind", "miles", "gain_ft", "loss_ft", "minutes", "title")}
                   | {"how": it["notes"] or "Your own run.", "detail": it["detail"], "own": True})
    return out


@app.get("/api/plan")
def get_plan() -> dict:
    store = get_store()
    day = date.today()
    th = store.thresholds()
    obj = store.objective()
    c = _constraints(store)
    acts = _activities(store)
    accepted = store.plan("accepted")
    out = {"date": day.isoformat(), "constraints": asdict(c), "objective": None, "readiness": None,
           "accepted": None, "draft": None, "sessions": [], "review": None,
           "fitness_only": planner.FITNESS_ONLY, "thresholds": _thresholds(store)}
    if obj is None:
        return out
    history = _plan_history(store, day)
    now = planner.readiness_now(obj, acts, _descent_fitness(history), day)
    draft = store.plan("draft")
    return out | {
        "objective": obj.json() | {"days_to_go": (obj.day - day).days},
        "readiness": {**_readiness_json(now), "demand": now["demand"], "capacity": now["capacity"],
                      "pace": now["pace"]},
        "accepted": _plan_json(accepted), "draft": _plan_json(draft),
        "sessions": _with_own_runs(store, _sessions(store, accepted, acts, day, th.aet if th else 150), acts),
        "review": _last_week_review(store, accepted, acts, day) if accepted else None,
        "this_week": _this_week(store, accepted, acts, day),
        "actuals": {} if accepted is None else {
            w["start"]: {k: a[k] for k in ("miles", "gain_ft", "loss_ft", "below_aet_pct")}
            for w in accepted["weeks"] if w["start"] <= day.isoformat()
            for a in [planner.week_actual(acts, date.fromisoformat(w["start"]))]},
    }


def _this_week(store: Store, accepted: dict | None, acts: list[dict], day: date) -> dict:
    """This week's target, done and still scheduled, as the calendar counts them."""
    start = day - timedelta(days=day.weekday())
    return schedule.weeks(_calendar(store, start, 7, acts), acts, accepted, start, 7, day)[0]


class ObjectiveIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    day: date
    distance_mi: float = Field(gt=0, le=300)
    gain_ft: float = Field(ge=0, le=80000)
    loss_ft: float = Field(ge=0, le=80000)
    longest_climb_ft: float | None = Field(default=None, ge=0, le=30000)
    longest_descent_ft: float | None = Field(default=None, ge=0, le=30000)
    high_point_ft: float | None = Field(default=None, ge=-1500, le=30000)
    source: str = Field(default="manual", pattern="^(manual|gpx)$")
    new: bool = False  # true replaces the objective; false edits the active one


@app.put("/api/plan/objective")
def put_objective(body: ObjectiveIn) -> dict:
    """Set the objective and draft a plan for it. Editing the active objective drafts a new plan
    shown against the accepted one; a new objective starts over."""
    if body.day <= date.today():
        raise HTTPException(422, "Pick an objective date after today.")
    store = get_store()
    fields = body.model_dump(exclude={"new"})
    fields["name"] = fields["name"].strip()
    current = store.objective()
    if current is None or body.new:
        obj = planner.Objective(**fields)
        obj.id = store.save_objective(obj)
        reason = "new"
    else:
        obj = planner.Objective(**fields, id=current.id)
        store.update_objective(obj)
        reason = "objective"
    try:
        _build_draft(store, obj, reason)
    except HTTPException as e:
        if e.status_code != 409:
            raise
    return get_plan()


@app.delete("/api/plan/objective")
def delete_objective() -> dict:
    get_store().clear_objective()
    return {"ok": True}


@app.post("/api/plan/route")
async def parse_route(request: Request) -> dict:
    """A GPX route's numbers, to fill in the objective form. The track itself is not kept."""
    data = await request.body()
    if len(data) > 20_000_000:
        raise HTTPException(413, "That file is over 20 MB.")
    try:
        return route_numbers(data)
    except Exception as e:
        raise HTTPException(422, str(e) if isinstance(e, ValueError) else "Could not read that GPX file.")


class ConstraintsIn(BaseModel):
    days: list[int] = Field(min_length=1, max_length=7)
    weekday_min: int = Field(ge=15, le=600)
    weekend_min: int = Field(ge=15, le=900)
    long_day: int = Field(ge=0, le=6)
    notes: str = Field(default="", max_length=500)
    back_to_back: bool | None = None  # left out: keep the current setting
    workout: str | None = Field(default=None, pattern="^(none|track|tempo|hill_repeats)$")  # left out: keep


@app.put("/api/plan/constraints")
def put_constraints(body: ConstraintsIn) -> dict:
    """Training days and time limits. With an objective set, a new plan is drafted from them."""
    if any(d < 0 or d > 6 for d in body.days):
        raise HTTPException(422, "Days are 0 (Monday) to 6 (Sunday).")
    if body.long_day not in body.days:
        raise HTTPException(422, "Your long day has to be one of your training days.")
    store = get_store()
    now = _constraints(store)
    keep = {"adjust": now.adjust} | {k: getattr(now, k) for k in ("back_to_back", "workout")
                                     if getattr(body, k) is None}
    c = planner.Constraints.from_json(body.model_dump(exclude_none=True) | keep)
    store.save_settings({"plan_constraints": asdict(c)})
    obj = store.objective()
    if obj is not None:
        _build_draft(store, obj, "constraints")
    return get_plan()


class DraftIn(BaseModel):
    reason: str = Field(default="request", pattern="^(request|missed|flag|pain)$")


@app.post("/api/plan/draft")
def new_draft(body: DraftIn) -> dict:
    store = get_store()
    obj = store.objective()
    if obj is None:
        raise HTTPException(409, "Add an objective first.")
    _build_draft(store, obj, body.reason)
    return get_plan()


# Planning with the coach: a conversation that fills the same inputs as the plan form. The
# questions and summaries are written by the engine; only typed answers go to the model, and only
# to be read into fields the engine then checks.

TALK_KEY = "plan_talk"
TALK_KEEP = 80  # messages kept


class TalkIn(BaseModel):
    text: str | None = Field(default=None, max_length=1000)
    answers: dict[str, str | list[str]] | None = None
    route: dict | None = None  # a GPX route's numbers from /api/plan/route
    action: str | None = Field(default=None, pattern="^(accept|keep|retry)$")


class TalkStart(BaseModel):
    mode: str = Field(default="setup", pattern="^(setup|change)$")


def _talk_new(store: Store, mode: str) -> dict:
    c = asdict(_constraints(store))
    obj = store.objective()
    if mode == "change" and obj is not None and (store.plan("accepted") or store.plan("draft")):
        return interview.change(obj.name, c)
    return interview.start(c, obj.json() if obj else None)


def _talk_state(store: Store, mode: str | None = None) -> dict:
    """The conversation in progress, or a fresh one when it ended or no longer fits the plan."""
    st = store.settings().get(TALK_KEY)
    has_plan = store.plan("accepted") is not None or store.plan("draft") is not None
    stale = (st is None or st.get("step") == "done" or (mode is not None and st.get("mode") != mode)
             or (st.get("step") == "draft" and not has_plan)
             or (st.get("mode") == "setup" and store.plan("accepted") is not None and st.get("step") != "draft"))
    if stale:
        st = _talk_new(store, mode or "setup")
        store.save_settings({TALK_KEY: st})
    return st


def _talk_json(store: Store, st: dict) -> dict:
    cfg = _coach_settings(store)
    return {"messages": st["messages"], "step": st["step"], "mode": st.get("mode", "setup"),
            "reads_with_ai": bool(cfg["coach_enabled"] and os.environ.get("ANTHROPIC_API_KEY"))}


def _talk_read(store: Store, mode: str, text: str, packet: dict, fallback: dict) -> tuple[dict, dict | None]:
    """Typed words read into fields by the cheaper model, or by the rules without it."""
    out = _coach(store, mode, mode, packet, fallback, False)
    return out["reply"], ({"cost": out["cost"]} if out["source"] == "coach" else None)


def _race_lookup(store: Store, text: str, day: date) -> tuple[dict, dict | None]:
    """A named race's date, distance and vert from the web, on the analysis model. Empty when
    the coach is off or over its cap, there's no key, or nothing usable was found."""
    cfg = _coach_settings(store)
    client = coach_client()
    cap = _coach_cap(store)
    if not cfg["coach_enabled"] or client is None or (cap["limit"] is not None and cap["spent"] >= cap["limit"]):
        return {}, None
    model_id = cfg["analysis_model"]
    try:
        found, model, usage = coach.lookup_race(client, text, day.isoformat(), model_id)
    except Exception as e:  # noqa: BLE001 - a failed lookup only means asking for the numbers
        import anthropic
        if not isinstance(e, (anthropic.APIError, ValueError)):
            raise
        return {}, None
    packet = {"today": day.isoformat(), "text": text}
    store.log_coach("lookup", "lookup", coach.PROMPT_VERSION, coach.cache_key(packet, model_id, coach.DEFAULT_STYLE),
                    packet, coach.Result("coach" if found else "rules", found or {}, model, 1,
                                         None if found else "not_found", [], usage))
    meta = {"cost": _shown_cost(model, usage, cached=False)}
    if not found:
        return {}, meta
    got = interview.from_intake(found, day)
    if isinstance(found.get("source"), str) and found["source"].startswith("http"):
        got["looked_up"] = found["source"][:300]
    elif got:
        got["looked_up"] = "web"
    return got, meta


def _talk_objective(store: Store, st: dict, body: TalkIn, day: date) -> list[dict]:
    o = dict(st["objective"])
    current = store.objective()
    meta = None
    if o.get("looked_up") and ((body.answers and body.answers.get("confirm")) or body.text):
        o["confirmed"] = True  # tapped "Use these", or typed what's different
    if body.answers and body.answers.get("keep") and current is not None:
        o = current.json() | {"keep": True}
    if body.route:
        o |= {k: body.route[k] for k in ("distance_mi", "gain_ft", "loss_ft", "longest_climb_ft", "longest_descent_ft",
                                         "high_point_ft") if isinstance(body.route.get(k), (int, float))}
        o["source"] = "gpx"
        o.pop("keep", None)
        if body.route.get("name") and not o.get("name"):
            o["name"] = str(body.route["name"])[:80]
    if body.text:
        got = interview.rules_objective(body.text, day)
        if interview.missing(o | got):
            read, meta = _talk_read(store, "intake", body.text, {"today": day.isoformat(), "text": body.text}, {})
            ai = interview.from_intake(read, day)
            name = ai.get("name") or got.get("name")  # the model reads names better; the rules, numbers
            got = ai | got | ({"name": name} if name else {})
        if o.get("name") and sum(k in got for k in ("day", "distance_mi", "gain_ft")) < 2:
            got.pop("name", None)  # filling in a missing piece, like "it's on March 3", keeps the name
        if got.get("name") and interview.missing(o | got) and not o.get("looked_up"):
            # A race named without its numbers: look it up, and ask before using what's found.
            found, lookup_meta = _race_lookup(store, body.text, day)
            meta = lookup_meta or meta
            filled = {k: v for k, v in found.items() if k not in got and k != "name"}
            if any(k in filled for k in ("day", "distance_mi", "gain_ft")):
                got |= filled
        o |= got
        o.pop("keep", None)
        if got.get("looked_up") and not interview.missing(o):
            o.pop("confirmed", None)
    problems = []
    if o.get("day") and date.fromisoformat(o["day"]) <= day:
        o.pop("day")
        problems.append("That date has passed; the objective needs a date after today.")
    st["objective"] = o
    need = interview.missing(o)
    if not need and o.get("looked_up") and not o.get("confirmed"):
        where = "" if o["looked_up"] == "web" else f" ({o['looked_up']})"
        return [interview.coach(f"I looked it up{where}: {interview.objective_line(o | {'loss_ft': o.get('loss_ft') or o['gain_ft']})}. "
                                "Use these, or type what's different.", "objective",
                                [{"key": "confirm", "options": ["Use these"], "value": None}]) | (meta or {})]
    if need:
        have = []
        if o.get("name"):
            have.append(o["name"])
        if o.get("distance_mi"):
            have.append(f"{o['distance_mi']:.1f} mi")
        if o.get("gain_ft"):
            have.append(f"{round(o['gain_ft']):,} ft up")
        if o.get("day"):
            d = date.fromisoformat(o["day"])
            have.append(f"{d.strftime('%b')} {d.day}")
        text = " ".join(problems + ([f"Got {', '.join(have)}."] if have else []) +
                        [f"I still need {interview._join_words([interview.MISSING_TEXT[k] for k in need])}."])
        return [interview.coach(text, "objective", st["messages"][-1].get("groups") if not body.text else []) | (meta or {})]
    o.setdefault("loss_ft", o["gain_ft"])
    st["step"] = "goal"
    return [interview.coach(f"Training for {interview.objective_line(o)}.") | (meta or {}),
            interview.ask("goal")]


def _talk_finish(store: Store, st: dict, day: date) -> list[dict]:
    """Save the answers as the objective and training days, then draft the plan."""
    a, o = st["answers"], st["objective"]
    c = _constraints(store)
    c.days = sorted(interview.DAY_SHORT.index(d) for d in a["days"])
    c.long_day = interview.DAY_SHORT.index(a["long_day"])
    c.weekday_min, c.weekend_min = a["weekday_min"], a["weekend_min"]
    c.notes = interview.notes(a)
    c.adjust = interview.lighter_start(day) if a.get("feel") == "Tired lately" else []
    store.save_settings({"plan_constraints": asdict(c), "plan_context": interview.context(a)})
    current = store.objective()
    fields = {k: o.get(k) for k in ("name", "distance_mi", "gain_ft", "loss_ft", "longest_climb_ft",
                                    "longest_descent_ft", "high_point_ft")} | {
        "day": date.fromisoformat(o["day"]), "source": o.get("source", "manual")}
    if o.get("keep") and current is not None:
        obj, reason = current, "constraints"
    else:
        obj = planner.Objective(**fields)
        obj.id = store.save_objective(obj)
        reason = "new"
    try:
        plan = _build_draft(store, obj, reason)
    except HTTPException as e:
        st["step"] = "stuck"
        return [interview.coach(str(e.detail), "stuck", actions=[{"id": "retry", "label": "Try again"}])]
    st["step"] = "draft"
    actions = [{"id": "accept", "label": "Accept plan"}]
    if store.plan("accepted") is not None:
        actions.append({"id": "keep", "label": "Keep my current plan"})
    return [interview.coach(interview.draft_summary(plan, obj.name, c.long_day, a), "draft", actions=actions)]


def _talk_change(store: Store, st: dict, text: str, day: date) -> list[dict]:
    obj = store.objective()
    before = store.plan("draft") or store.plan("accepted")
    if obj is None or before is None:
        st["step"] = "done"
        return [interview.coach("There's no plan to change. Start a new one from the Plan page.")]
    c = _constraints(store)
    packet = {"today": day.isoformat(), "text": text, "training_days": c.days, "long_day": c.long_day,
              "weeks_in_plan": len(before["weeks"])}
    read, meta = _talk_read(store, "edit", text, packet, {"edits": interview.rules_edits(text)})
    edits = read["edits"] if meta is None else interview.from_edits(read["edits"])
    if not edits:
        asked = read.get("cannot") if meta is not None and isinstance(read.get("cannot"), str) else None
        return [interview.coach(interview.cant_text(asked), "draft") | (meta or {})]
    new_c, done, skipped = interview.apply_edits(edits, asdict(c), before["weeks"])
    if not done:
        return [interview.coach(interview.change_text(done, skipped, []) + "\n" + interview.EDIT_HELP, "draft") | (meta or {})]
    store.save_settings({"plan_constraints": asdict(planner.Constraints.from_json(new_c))})
    try:
        plan = _build_draft(store, obj, "constraints")
    except HTTPException as e:
        return [interview.coach(str(e.detail), "draft") | (meta or {})]
    st["step"] = "draft"
    actions = [{"id": "accept", "label": "Accept plan"}]
    if store.plan("accepted") is not None:
        actions.append({"id": "keep", "label": "Keep my current plan"})
    return [interview.coach(interview.change_text(done, skipped, planner.diff(before, plan)), "draft", actions=actions)
            | (meta or {})]


@app.get("/api/plan/talk")
def get_talk(mode: str | None = None) -> dict:
    if mode not in (None, "setup", "change"):
        raise HTTPException(422, "Mode is setup or change.")
    store = get_store()
    return _talk_json(store, _talk_state(store, mode))


@app.post("/api/plan/talk/restart")
def restart_talk(body: TalkStart) -> dict:
    store = get_store()
    st = _talk_new(store, body.mode)
    store.save_settings({TALK_KEY: st})
    return _talk_json(store, st)


@app.post("/api/plan/talk")
def post_talk(body: TalkIn) -> dict:
    """One turn: chip answers, typed words, a GPX route or an action on the draft."""
    store = get_store()
    day = date.today()
    st = _talk_state(store)
    step = st["step"]
    text = (body.text or "").strip()
    body.text = text or None
    answers = {k: v for k, v in (body.answers or {}).items() if v not in (None, "", [])}
    if body.action == "accept" or body.action == "keep":
        line = "Accept plan" if body.action == "accept" else "Keep my current plan"
    elif body.route:
        line = f"Uploaded a route: {body.route.get('distance_mi', 0):.1f} mi, {round(body.route.get('gain_ft', 0)):,} ft up"
    else:
        line = " · ".join(x for x in (interview.said(step, answers), text) if x)
    if not line and body.action != "retry":
        raise HTTPException(422, "Say something, or tap an answer.")
    if line:
        st["messages"].append({"role": "athlete", "text": line})
    if body.action in ("accept", "keep"):
        draft = store.plan("draft")
        if draft is None:
            reply = [interview.coach("There's no draft waiting. Your plan is as it was.")]
        else:
            store.decide_draft(draft["id"], body.action == "accept")
            reply = [interview.coach("Done. Your plan is on the calendar, and you can change it here any time."
                                     if body.action == "accept" else "Kept your current plan.")]
        st["step"] = "done"
    elif body.action == "retry" or step == "stuck":
        reply = _talk_finish(store, st, day)
    elif step == "objective":
        reply = _talk_objective(store, st, body, day)
    elif step in interview.STEPS:
        got, problem = interview.chip_answers(step, answers, text, st["defaults"])
        if problem:
            reply = [interview.coach(problem, step, [g | {"value": answers.get(g["key"]) or st["defaults"].get(g["key"])}
                                                     for g in interview.QUESTIONS[step]["groups"]])]
        else:
            st["answers"] |= got
            nxt = interview.next_step(step)
            if nxt is None:
                reply = _talk_finish(store, st, day)
            else:
                st["step"] = nxt
                reply = [interview.ask(nxt, st["defaults"])]
    else:  # a draft is up: typed changes
        if not text:
            raise HTTPException(422, "Tell me what to change.")
        reply = _talk_change(store, st, text, day)
    st["messages"] = (st["messages"] + reply)[-TALK_KEEP:]
    store.save_settings({TALK_KEY: st})
    return _talk_json(store, st)


@app.post("/api/plan/{plan_id}/{decision}")
def decide_plan(plan_id: int, decision: str) -> dict:
    if decision not in ("accept", "decline"):
        raise HTTPException(404, "Accept or decline.")
    if not get_store().decide_draft(plan_id, decision == "accept"):
        raise HTTPException(404, "No draft plan with that id.")
    return get_plan()


def _plan_for_coach(store: Store) -> dict | None:
    return store.plan("draft") or store.plan("accepted")


@app.get("/api/coach/plan")
def coach_plan(refresh: bool = False) -> dict:
    store = get_store()
    p = _plan_for_coach(store)
    if p is None:
        raise HTTPException(404, "No plan yet.")
    packet = coach.plan_packet(p, p["now"], date.fromisoformat(p["built_on"]),
                               planner.REPLAN_TEXT.get(p["reason"], p["reason"]), p.get("changes") or [])
    if answers := store.settings().get("plan_context"):
        packet["athlete_answers"] = answers  # tap answers from planning with the coach; no typed text
    return _coach(store, "plan", f"plan-{p['id']}", packet, coach.rules_plan(packet), refresh) | {"plan_id": p["id"]}


@app.get("/api/coach/review")
def coach_review(refresh: bool = False) -> dict:
    store = get_store()
    day = date.today()
    accepted = store.plan("accepted")
    if accepted is None:
        raise HTTPException(404, "No accepted plan yet.")
    review = _last_week_review(store, accepted, _activities(store), day)
    next_week = planner.plan_for_day(accepted, day + timedelta(days=7 - day.weekday()))
    obj = coach._objective(accepted, day)
    packet = coach.review_packet(review, next_week, {"date": obj["date"], "days_to_go": obj["days_to_go"]},
                                 review["replan_text"])
    return _coach(store, "review", review["start"], packet, coach.rules_review(packet), refresh) | {"review": review}


# Scheduled runs

@app.get("/api/schedule")
def get_schedule(start: date | None = None, days: int = 35) -> dict:
    """The calendar: the athlete's scheduled runs and the plan's next 7 days, with what each
    matched, plus weeks the scheduled runs would push too fast."""
    store = get_store()
    day = date.today()
    start = start or day - timedelta(days=day.weekday())
    days = max(1, min(days, 120))
    acts = _activities(store, min(start, day) - timedelta(days=planner.CAPACITY_DAYS))
    items = _calendar(store, start, days, acts)
    return {"date": day.isoformat(), "start": start.isoformat(), "days": days, "items": items,
            "activities": schedule.unscheduled(items, acts, start, days),
            "weeks": schedule.weeks(items, acts, store.plan("accepted"), start, days, day),
            "warnings": _projection(store, day)["warnings"],
            "kinds": [{"key": k, "label": v} for k, v in schedule.KINDS.items()],
            "zone_model": _zone_model_json(_zone_model(store))}


class PlannedRunIn(BaseModel):
    day: date
    kind: str = Field(pattern="^(" + "|".join(schedule.KINDS) + ")$")
    miles: float = Field(ge=0, le=200)
    gain_ft: float = Field(default=0, ge=0, le=40000)
    loss_ft: float | None = Field(default=None, ge=0, le=40000)  # left out: same as gain
    minutes: float | None = Field(default=None, gt=0, le=2880)
    zone: int | None = Field(default=None, ge=1, le=5)
    notes: str = Field(default="", max_length=300)


def _run(body: PlannedRunIn, run_id: int | None = None) -> schedule.PlannedRun:
    day = date.today()
    if body.day < day - timedelta(days=7) or body.day > day + timedelta(days=schedule.MAX_AHEAD_DAYS):
        raise HTTPException(422, "Schedule runs from a week ago to a year ahead.")
    if body.miles == 0 and not body.minutes:
        raise HTTPException(422, "Give the run a distance or a time.")
    return schedule.PlannedRun(body.day, body.kind, body.miles, body.gain_ft,
                               body.gain_ft if body.loss_ft is None else body.loss_ft, body.minutes, body.zone,
                               body.notes.strip(), run_id)


@app.post("/api/schedule")
def add_planned_run(body: PlannedRunIn) -> dict:
    store = get_store()
    return {"id": store.save_planned_run(_run(body))}


@app.put("/api/schedule/{run_id}")
def change_planned_run(run_id: int, body: PlannedRunIn) -> dict:
    """Edit a scheduled run; changing its day moves it."""
    if get_store().save_planned_run(_run(body, run_id)) is None:
        raise HTTPException(404, "No such scheduled run.")
    return {"id": run_id}


@app.delete("/api/schedule/{run_id}")
def delete_planned_run(run_id: int) -> dict:
    if not get_store().delete_planned_run(run_id):
        raise HTTPException(404, "No such scheduled run.")
    return {"ok": True}


CHAT_HISTORY = 6  # earlier turns sent with each question


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@app.get("/api/coach/chat")
def get_chat() -> dict:
    return {"messages": _chat_costs(get_store().chat())}


@app.delete("/api/coach/chat")
def clear_chat() -> dict:
    get_store().clear_chat()
    return {"ok": True}


CHAT_UNAVAILABLE = {
    "no_key": "The AI coach isn't set up: add an Anthropic API key to the engine's .env to chat.",
    "off": "The AI coach is turned off in Settings, so it can't answer questions.",
    "cap": "This month's coach spending limit is reached, so chat is paused until next month.",
}


@app.post("/api/coach/chat")
def post_chat(body: ChatIn) -> dict:
    store = get_store()
    day = date.today()
    question = body.question.strip()
    history = store.chat(CHAT_HISTORY)
    try:
        t = today()
    except HTTPException:
        t = None
    accepted = store.plan("accepted")
    obj = store.objective()
    acts = _activities(store)
    now = None
    if obj is not None:
        now = _readiness_json(planner.readiness_now(obj, acts, _descent_fitness(_plan_history(store, day)), day))
    plan_ctx = accepted if accepted is not None and obj is not None else None
    sessions = _with_own_runs(store, _sessions(store, plan_ctx, acts, day, t["thresholds"]["aet"] if t else 150), acts)
    packet = coach.chat_packet(question, history, t, plan_ctx, now if plan_ctx else None, sessions,
                               [a for a in acts if a["day"] >= (day - timedelta(days=13)).isoformat()], day, acts)
    store.add_chat("athlete", question)
    out = _coach(store, "chat", "chat", packet, {"answer": coach.CHAT_FALLBACK, "used": []}, False)
    answer = CHAT_UNAVAILABLE.get(out["fallback_reason"] or "", out["reply"]["answer"])
    store.add_chat("coach", answer, out["source"], out["log_id"])
    return {"messages": _chat_costs(store.chat()), "source": out["source"], "fallback_reason": out["fallback_reason"]}


# Settings. Keys stay in .env: the engine reports whether each one is set, never its value.

INFER_DAYS = 180
# Folders whose runs are being read for the threshold estimate, by a background thread each.
_inferring: dict[str, threading.Thread] = {}
_inferring_guard = threading.Lock()


def start_inferring(folder) -> None:
    """Read any runs without stored inference features, in the background, once per folder at a
    time. Settings shows what is stored meanwhile."""
    key = str(folder)

    def run() -> None:
        try:
            cli.fill_infer_features(Store(folder), INFER_DAYS)
        finally:
            with _inferring_guard:
                _inferring.pop(key, None)

    with _inferring_guard:
        if key in _inferring:
            return
        _inferring[key] = threading.Thread(target=run, name=f"infer {key}", daemon=True)
        _inferring[key].start()


def _inferred_thresholds(store: Store) -> dict | None:
    """Thresholds estimated from the last 180 days of runs (talus infer), with how far to trust
    each one and the runs it came from. Built from each run's stored features, so it is quick;
    runs not read yet are read in the background and `status` says "computing" until they are."""
    r, rows, missing = cli.inferred(store, INFER_DAYS)
    if missing:
        start_inferring(store.dir)
    if not rows:
        return None
    status = {"status": "computing" if missing else "ready", "runs_read": len(rows) - missing,
              "runs_total": len(rows)}
    if not r["runs_with_hr"]:
        return None if not missing else status | {"days": INFER_DAYS, "runs_with_hr": 0}
    info = {row["id"]: {"id": row["id"], "day": row["start_time"][:10], "name": row["name"]} for row in rows}
    return status | {
        "days": INFER_DAYS, "runs_with_hr": r["runs_with_hr"],
        **{k: {"value": e.value, "confidence": e.confidence, "method": e.method,
               "activities": [info[i] for i in e.activities if i in info]}
           for k, e in ((k, r[k]) for k in ("aet", "ant", "max"))}}


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
            "athlete_name": cred and cred.get("athlete_name"),
            "oauth_available": intervals_auth.enabled(),
            "updated_at": cred and cred["updated_at"],
            "garmin_fallback_set": _is_owner() and bool(os.environ.get("GARMIN_EMAIL")),
            "activities": len(acts),
            "latest_activity": max((r["start_time"][:10] for r in acts), default=None),
            "last_sync": saved.get("last_sync"),
        },
        "sources": _sources(saved),
        "sync_method": saved.get("sync_method", "intervals"),
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


class SyncMethodIn(BaseModel):
    method: Literal["intervals", "watch"]


@app.put("/api/settings/sync-method")
def put_sync_method(body: SyncMethodIn) -> dict:
    """How new activities arrive: intervals.icu's API, or straight off a Garmin watch over
    Bluetooth from the web (Chrome) or iOS app. File upload works either way."""
    get_store().save_settings({"sync_method": body.method})
    return {"ok": True}


def _intervals_credential() -> dict | None:
    return cli.credential_for(get_accounts(), _account.get())


class SourceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)  # so 8 spaces are not a key
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
    """Forget this account's intervals.icu key or connection. A connection is also ended on
    intervals.icu's side, so Talus leaves its list of connected apps."""
    accounts, account = get_accounts(), current_account()
    cred = accounts.credential(account.id, "intervals")
    accounts.clear_credential(account.id, "intervals")
    if cred and cred["kind"] == "oauth":
        intervals_auth.disconnect(cred["access_token"])
    return {"ok": True}


def _sources(saved: dict) -> dict:
    """Every way data can reach this account, for the Data sources card. Sync sources carry
    their own last sync; uploads share the last import."""
    accounts, account = get_accounts(), _account.get()
    garmin = accounts.credential(account.id, "garmin") if account else None
    return {
        "intervals": {"connected": _intervals_credential() is not None,
                      "last_sync": saved.get("last_sync_intervals")
                      or (saved.get("last_sync") if (saved.get("last_sync") or {}).get("source") == "intervals" else None)},
        "garmin": {"available": _is_owner(), "connected": garmin is not None,
                   "updated_at": garmin and garmin["updated_at"],
                   "mfa_pending": bool(account) and _garmin().mfa_pending(account.id),
                   "last_sync": saved.get("last_sync_garmin")},
        "upload": {"last_import": saved.get("last_import"), "max_mb": MAX_UPLOAD_MB},
        "healthkit": {"last_sync": saved.get("last_sync_healthkit")},
        "watch": {"last_sync": saved.get("last_sync_watch")},
    }


def _garmin():
    from .sources import garmin
    return garmin


class SyncIn(BaseModel):
    days: int = Field(default=30, ge=1, le=730)
    source: str = Field(default="intervals", pattern="^(intervals|garmin)$")


@app.post("/api/sync")
def sync_now(body: SyncIn, background: BackgroundTasks) -> dict:
    store = get_store()
    save = None
    if body.source == "intervals":
        cred = _intervals_credential()
        if cred is None:
            raise HTTPException(409, "Connect intervals.icu under Data sources first.")
    else:
        account = current_account()
        if not account.is_owner:
            raise HTTPException(403, "Garmin Connect is for the owner account only.")
        cred = get_accounts().credential(account.id, "garmin")
        if cred is None:
            raise HTTPException(409, "Connect Garmin under Data sources first.")

        def save(values: dict) -> None:
            get_accounts().set_credential(account.id, "garmin", "tokens", values)
    lines: list[str] = []
    try:
        result = cli.sync(store, body.source, date.today() - timedelta(days=body.days), log=lines.append,
                          credential=cred, save_credential=save)
    except Exception:
        return store.settings()[f"last_sync_{body.source}"] | {"log": lines}
    # The coach writes after the response, so the sync button doesn't wait on it.
    background.add_task(warm_coach, result.pop("added_ids"))
    background.add_task(start_inferring, store.dir)
    return result | {"log": lines}


# First run. A new account is walked through six steps; only connecting a source is required.
# Progress lives in the account's own settings (`onboarding`), so the flow resumes where it
# stopped, and an account already set up before the flow existed never sees it.

ONBOARDING_STEPS = ("connect", "import", "thresholds", "sleep", "coach", "tour")
OPTIONAL_STEPS = ("thresholds", "sleep", "coach", "tour")
IMPORT_DAYS = {"6w": 42, "6m": 180, "all": 730}

_imports: dict[str, dict] = {}
_imports_guard = threading.Lock()


def _resting_hint(store: Store) -> int | None:
    """A resting heart rate to start from: the median of the last 30 nights' readings, if any."""
    nights = store.wellness(date.today() - timedelta(days=30), date.today())
    values = sorted(n["resting_hr"] for n in nights.values() if n["resting_hr"])
    return round(values[len(values) // 2]) if values else None


def _import_status(store: Store) -> dict | None:
    with _imports_guard:
        live = _imports.get(str(store.dir))
    return dict(live) if live else store.settings().get("onboarding_import")


def _onboarding(store: Store) -> dict:
    saved = store.settings()
    ob = saved.get("onboarding") or {}
    th = store.thresholds()
    activities = store.db.execute("SELECT COUNT(*) FROM activity WHERE start_time IS NOT NULL").fetchone()[0]
    if not ob and th is not None and activities:
        # Set up before this flow existed: recorded once as finished, so it never opens on its own.
        ob = {"finished": datetime.now().isoformat(timespec="seconds"), "established": True}
        store.save_settings({"onboarding": ob})
    done, skipped = set(ob.get("done") or []), set(ob.get("skipped") or [])
    accounts, account = get_accounts(), _account.get()
    garmin = bool(account) and accounts.credential(account.id, "garmin") is not None
    intervals = _intervals_credential() is not None
    imp = _import_status(store)
    state = {
        "connect": intervals or garmin or activities > 0,
        "import": activities > 0 or bool(imp and imp.get("state") == "done" and not imp.get("error")),
        "thresholds": th is not None,
        "sleep": wellness.sleep_need(saved)[1] == "saved",
        "coach": "coach" in done,
        "tour": "tour" in done,
    }
    steps = [{"key": k, "done": state[k] or k in done, "skipped": k in skipped and not state[k],
              "required": k not in OPTIONAL_STEPS} for k in ONBOARDING_STEPS]
    nxt = next((s["key"] for s in steps if not s["done"] and not s["skipped"]), None)
    return {
        "needed": not ob.get("finished"),
        "started": ob.get("started"), "finished": ob.get("finished"),
        "established": bool(ob.get("established")),
        "steps": steps, "next": nxt,
        "import": imp, "activities": activities,
        "sources": {"intervals": intervals, "garmin": garmin, "garmin_available": _is_owner()},
        "resting_hint": _resting_hint(store),
        "coach": {"enabled": _coach_settings(store)["coach_enabled"], "style": _coach_settings(store)["coach_style"],
                  "key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
                  "styles": [{"key": k, "label": v["label"], "description": v["description"]}
                             for k, v in coach.STYLES.items()]},
        "import_choices": IMPORT_DAYS,
    }


@app.get("/api/onboarding")
def get_onboarding() -> dict:
    return _onboarding(get_store())


class OnboardingIn(BaseModel):
    action: str = Field(pattern="^(start|done|skip|finish|restart)$")
    step: str | None = None
    coach_enabled: bool | None = None
    coach_style: str | None = None


@app.put("/api/onboarding")
def put_onboarding(body: OnboardingIn) -> dict:
    """start: the flow was opened. done or skip: one step, with the coach choice on the coach
    step. finish: land on Today, also how "skip setup" ends it. restart: open it again."""
    store = get_store()
    _onboarding(store)  # records an account set up before this flow as established
    ob = dict(store.settings().get("onboarding") or {})
    now = datetime.now().isoformat(timespec="seconds")
    if body.action in ("done", "skip"):
        if body.step not in ONBOARDING_STEPS:
            raise HTTPException(422, f"Unknown step {body.step}")
        if body.action == "skip" and body.step not in OPTIONAL_STEPS:
            raise HTTPException(422, "Connect a data source and import some history first; those can't be skipped.")
        if body.step == "coach" and body.action == "done":
            if body.coach_style is not None and body.coach_style not in coach.STYLES:
                raise HTTPException(422, f"Unknown style {body.coach_style}")
            values = {"coach_enabled": bool(body.coach_enabled)}
            if body.coach_style is not None:
                values["coach_style"] = body.coach_style
            store.save_settings(values)
        if body.step == "coach" and body.action == "skip" and not ob.get("established"):
            # The spec's default without consent: load tracking only. Settings can turn it on.
            store.save_settings({"coach_enabled": False})
        key, other = ("done", "skipped") if body.action == "done" else ("skipped", "done")
        ob[key] = sorted(set(ob.get(key) or []) | {body.step})
        ob[other] = sorted(set(ob.get(other) or []) - {body.step})
    if body.action == "start" or not ob.get("started"):
        ob["started"] = ob.get("started") or now
    if body.action == "finish":
        ob["finished"] = now
        if not ob.get("established") and "coach" not in set(ob.get("done") or []) | set(ob.get("skipped") or []):
            store.save_settings({"coach_enabled": False})  # left setup before consenting
    if body.action == "restart":
        ob.pop("finished", None)
        ob["started"] = now
    store.save_settings({"onboarding": ob})
    return _onboarding(store)


class ImportIn(BaseModel):
    source: str = Field(default="intervals", pattern="^(intervals|garmin)$")
    range: str = Field(default="6m", pattern="^(6w|6m|all)$")


@app.post("/api/onboarding/import")
def start_import(body: ImportIn) -> dict:
    """Pull history from a synced source in the background, so the athlete can carry on with the
    next steps while it runs. GET /api/onboarding reports the progress."""
    store = get_store()
    account = current_account()
    save = None
    if body.source == "intervals":
        cred = _intervals_credential()
        if cred is None:
            raise HTTPException(409, "Connect intervals.icu first.")
    else:
        if not account.is_owner:
            raise HTTPException(403, "Garmin Connect is for the owner account only.")
        cred = get_accounts().credential(account.id, "garmin")
        if cred is None:
            raise HTTPException(409, "Connect Garmin first.")

        def save(values: dict) -> None:
            get_accounts().set_credential(account.id, "garmin", "tokens", values)
    key, folder, days = str(store.dir), store.dir, IMPORT_DAYS[body.range]
    status = {"state": "running", "source": body.source, "range": body.range, "days": days,
              "started": datetime.now().isoformat(timespec="seconds"), "done": 0, "total": None,
              "saved": 0, "error": None}

    def progress(done: int, total: int) -> None:
        with _imports_guard:
            _imports[key].update(done=done, total=total)

    def run() -> None:
        s = Store(folder)
        try:
            result = cli.sync(s, body.source, date.today() - timedelta(days=days), log=lambda _: None,
                              credential=cred, save_credential=save, progress=progress)
            final = {"saved": result["saved"], "duplicates": result["duplicates"],
                     "wellness_days": result.get("wellness_days", 0)}
        except Exception as e:
            final = {"error": f"{type(e).__name__}: {e}"}
        with _imports_guard:
            done = _imports.pop(key) | final | {"state": "done", "finished": datetime.now().isoformat(timespec="seconds")}
        s.save_settings({"onboarding_import": done})
        start_inferring(folder)

    with _imports_guard:
        if key in _imports:
            return dict(_imports[key])
        _imports[key] = status
        threading.Thread(target=run, name=f"import {key}", daemon=True).start()
    return status


class GarminIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class MfaIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


def _garmin_owner() -> Account:
    account = current_account()
    if not account.is_owner:
        raise HTTPException(403, "Garmin Connect is for the owner account only.")
    return account


@app.post("/api/sources/garmin")
def connect_garmin(body: GarminIn) -> dict:
    """Sign in to Garmin Connect once. The password is used for this sign-in and dropped; only
    the token bundle is kept, encrypted. Garmin may ask for an MFA code next."""
    account = _garmin_owner()
    garmin = _garmin()
    try:
        tokens = garmin.start_login(account.id, body.email.strip(), body.password)
    except garmin.GarminLoginError as e:
        raise HTTPException(400, str(e))
    if tokens is None:
        return {"status": "needs_mfa"}
    get_accounts().set_credential(account.id, "garmin", "tokens", {"tokens": tokens})
    return {"status": "connected"}


@app.post("/api/sources/garmin/mfa")
def garmin_mfa(body: MfaIn) -> dict:
    account = _garmin_owner()
    garmin = _garmin()
    try:
        tokens = garmin.finish_login(account.id, body.code)
    except garmin.GarminLoginError as e:
        raise HTTPException(400, str(e))
    get_accounts().set_credential(account.id, "garmin", "tokens", {"tokens": tokens})
    return {"status": "connected"}


@app.delete("/api/sources/garmin")
def disconnect_garmin() -> dict:
    """Forget the Garmin tokens. Activities already synced stay."""
    get_accounts().clear_credential(current_account().id, "garmin")
    return {"ok": True}


class HealthKitWorkout(BaseModel):
    uuid: str = Field(min_length=1, max_length=64)
    sport: str = Field(pattern="^(run|trail_run|hike|walk|ride|other)$")
    name: str | None = Field(default=None, max_length=200)
    start: str = Field(min_length=10, max_length=40)  # ISO 8601 with offset
    end: str = Field(min_length=10, max_length=40)
    device_name: str | None = Field(default=None, max_length=200)
    tcx: str = Field(min_length=20)


class HealthKitNight(BaseModel):
    day: date
    hrv_sdnn_ms: float | None = None  # accepted, not used: SDNN is not the rMSSD readiness reads
    resting_hr: float | None = Field(default=None, ge=20, le=150)
    sleep_s: float | None = Field(default=None, ge=0, le=86400)


class HealthKitIn(BaseModel):
    workouts: list[HealthKitWorkout] = Field(default_factory=list, max_length=500)
    wellness: list[HealthKitNight] = Field(default_factory=list, max_length=1000)


@app.post("/api/import/healthkit")
def import_healthkit(body: HealthKitIn, background: BackgroundTasks) -> dict:
    """What the Talus iOS app reads from HealthKit, for the signed-in account. See imports.import_healthkit."""
    from . import imports
    store = get_store()
    result = imports.import_healthkit(store, [w.model_dump() for w in body.workouts],
                                      [n.model_dump(mode="json") for n in body.wellness], log=lambda *_: None)
    background.add_task(warm_coach, result.pop("added_ids"))
    background.add_task(start_inferring, store.dir)
    return result


MAX_UPLOAD_MB = int(os.environ.get("TALUS_MAX_UPLOAD_MB", "2048"))


@app.post("/api/import")
async def import_file(request: Request, background: BackgroundTasks, name: str = "upload",
                      watch_file: str | None = None) -> dict:
    """Upload one file as the request body: a FIT, GPX or TCX file (gzipped or not), a zip of
    them, or the Health app's export.zip. Streamed to disk, since an Apple Health export can run
    to gigabytes, and deleted once read.

    watch_file is set when the web or iOS app pulled the file off a Garmin watch over Bluetooth:
    its key (type, file number and time) is remembered so the next sync skips it."""
    store = get_store()
    tmp_dir = store.dir / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{int(time.time() * 1000)}-{os.getpid()}.part"
    size = 0
    try:
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"That file is over {MAX_UPLOAD_MB} MB.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "The file was empty.")
        from starlette.concurrency import run_in_threadpool

        from .imports import import_paths
        lines: list[str] = []
        try:
            # A store's connection belongs to the thread that opened it.
            result = await run_in_threadpool(
                lambda: import_paths(Store(store.dir), [(os.path.basename(name), tmp)], lines.append))
        except Exception:
            return store.settings()["last_import"] | {"log": lines}
    finally:
        tmp.unlink(missing_ok=True)
    if watch_file and not result.get("error") and not result.get("failed"):
        remember_watch_file(store, watch_file)
    background.add_task(warm_coach, result.pop("added_ids"))
    background.add_task(start_inferring, store.dir)
    return result | {"log": lines}


WATCH_KEYS_KEPT = 2000


def remember_watch_file(store: Store, key: str) -> None:
    saved = store.settings()
    keys = [k for k in saved.get("watch_files", []) if k != key] + [key]
    store.save_settings({"watch_files": keys[-WATCH_KEYS_KEPT:],
                         "last_sync_watch": datetime.now().isoformat(timespec="seconds")})


@app.get("/api/import/watch")
def watch_files() -> dict:
    """Files already pulled off the watch, so the apps download only what is new."""
    saved = get_store().settings()
    return {"files": saved.get("watch_files", []), "last_sync": saved.get("last_sync_watch")}


# The analysis is written right after a sync only for recent activities: a first sync of a
# year of history shouldn't pay for a read of every old run. Older ones get theirs when opened.
WARM_ANALYSIS_DAYS = 14


def warm_coach(activity_ids: list[int]) -> list[str]:
    """Write today's brief and each new recent activity's read right after a sync, so the pages
    open with them ready. Replies are cached as usual, so opening a page doesn't pay again."""
    warm_details(get_store())
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
    streams = _streams(store, row)
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


DISABLED = "This account is turned off. Ask the owner of this Talus."


def _signup_open(accounts: Accounts) -> bool:
    """Anyone who can reach the app can make an account, unless sign-ups are closed: from the admin
    page once it has set them, else TALUS_SIGNUP=closed. The first account can always be made, so a
    fresh or upgraded install can be claimed."""
    if accounts.count() == 0:
        return True
    chosen = accounts.config("signup_open")
    return chosen if chosen is not None else os.environ.get("TALUS_SIGNUP", "open").lower() != "closed"


def _signed_in(response: Response, accounts: Accounts, account: Account) -> dict:
    response.set_cookie(COOKIE, accounts.start_session(account.id), max_age=SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", path="/",
                        secure=os.environ.get("TALUS_SECURE_COOKIE", "") == "1")
    return _account_json(account)


def _account_json(account: Account) -> dict:
    return {"id": account.id, "email": account.email, "is_owner": account.is_owner,
            "is_admin": account.is_admin, "created_at": account.created_at}


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    accounts = get_accounts()
    account = accounts.session_account(request.cookies.get(COOKIE))
    return {"account": account and _account_json(account), "has_accounts": accounts.count() > 0,
            "signup_open": _signup_open(accounts), "existing_data": accounts.legacy_data_waiting(),
            "google": google_auth.enabled()}


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
    if account.disabled_at:
        raise HTTPException(403, DISABLED)
    return _signed_in(response, accounts, account)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    get_accounts().end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/account")
def get_account() -> dict:
    accounts = get_accounts()
    account = current_account()
    return _account_json(account) | {"has_password": accounts.has_password(account.id),
                                     "google": accounts.identity(account.id, "google"),
                                     "google_available": google_auth.enabled()}


class PasswordIn(BaseModel):
    current: str = Field(max_length=256)
    new: str = Field(max_length=256)


@app.put("/api/account/password")
def change_password(body: PasswordIn, response: Response) -> dict:
    """Changing the password signs out every session, this one included, then signs this one back in."""
    accounts = get_accounts()
    account = current_account()
    # An account made with Google has no password yet; it can set one without the old one.
    if accounts.has_password(account.id) and accounts.authenticate(account.email, body.current) is None:
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
    """Delete the account and all its data. Needs the password again (or, for an account with no
    password, its email typed out); cannot be undone."""
    accounts = get_accounts()
    account = current_account()
    if accounts.has_password(account.id):
        if accounts.authenticate(account.email, body.password) is None:
            raise HTTPException(403, "Your password is wrong.")
    elif body.password.strip().lower() != account.email.lower():
        raise HTTPException(403, "Type your email exactly to confirm.")
    accounts.delete(account.id)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# Sign in with Google. The state, nonce and where to go next ride in a short-lived encrypted
# cookie, so a callback only completes in the browser that started it.

OAUTH_COOKIE = "talus_oauth"
OAUTH_TTL_S = 600


def _safe_next(path: str | None) -> str:
    return path if path and path.startswith("/") and not path.startswith("//") else "/"


def _back(path: str, error: str | None = None, param: str = "google_error",
          cookie: str = OAUTH_COOKIE) -> RedirectResponse:
    from urllib.parse import urlencode

    target = path if error is None else f"{path}{'&' if '?' in path else '?'}{urlencode({param: error})}"
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(cookie, path="/")
    return response


@app.get("/api/auth/google/start")
def google_start(request: Request, next: str | None = None, link: bool = False) -> RedirectResponse:
    """Send the browser to Google. With link=1, a signed-in account adds Google to itself."""
    if not google_auth.enabled():
        return _back("/login", "Google sign-in isn't set up on this Talus.")
    accounts = get_accounts()
    link_to = None
    if link:
        account = accounts.session_account(request.cookies.get(COOKIE))
        if account is None:
            return _back("/login", "Sign in first.")
        link_to = account.id
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    sealed = accounts._cipher().encrypt(json.dumps(
        {"state": state, "nonce": nonce, "next": _safe_next(next), "link": link_to}).encode()).decode()
    response = RedirectResponse(google_auth.authorize_url(state, nonce), status_code=303)
    response.set_cookie(OAUTH_COOKIE, sealed, max_age=OAUTH_TTL_S, httponly=True, samesite="lax", path="/",
                        secure=os.environ.get("TALUS_SECURE_COOKIE", "") == "1")
    return response


@app.get(google_auth.CALLBACK_PATH)
def google_callback(request: Request, code: str | None = None, state: str | None = None,
                    error: str | None = None) -> RedirectResponse:
    """Google sends the browser back here. A Google account already linked signs in; one whose
    verified email matches a Talus account links to it and signs in; otherwise a new account is
    made, if sign-ups are open."""
    accounts = get_accounts()
    try:
        flow = json.loads(accounts._cipher().decrypt(request.cookies.get(OAUTH_COOKIE, "").encode(),
                                                     ttl=OAUTH_TTL_S))
    except Exception:
        return _back("/login", "The Google sign-in timed out. Try again.")
    home = "/settings" if flow["link"] else "/login"
    if error or not code:
        return _back(home, "Google sign-in was cancelled.")
    if not state or not secrets.compare_digest(state, flow["state"]):
        return _back(home, "The sign-in didn't match this browser. Try again.")
    try:
        subject, email = google_auth.check_claims(google_auth.exchange_code(code), flow["nonce"])
    except google_auth.GoogleError as e:
        return _back(home, str(e))
    except httpx.HTTPError:
        return _back(home, "Couldn't reach Google. Try again.")

    if flow["link"]:
        current = accounts.session_account(request.cookies.get(COOKIE))
        if current is None or current.id != flow["link"]:
            return _back("/login", "Sign in first.")
        try:
            accounts.link_identity(current.id, "google", subject, email)
        except AccountError as e:
            return _back("/settings", str(e))
        return _back("/settings")

    account = accounts.identity_account("google", subject)
    if account is None:
        account = accounts.by_email(email)
        if account is None:
            if not _signup_open(accounts):
                return _back("/login", "New accounts are closed on this Talus. Ask its owner.")
            account = accounts.create(email, None)
        accounts.link_identity(account.id, "google", subject, email)
    if account.disabled_at:
        return _back("/login", DISABLED)
    response = _back(flow["next"])
    _signed_in(response, accounts, account)
    return response


# Connect intervals.icu with OAuth. Same sealed-cookie pattern as Google: the callback only
# completes in the browser, and for the account, that started it.

INTERVALS_COOKIE = "talus_intervals_oauth"


def _intervals_back(error: str | None = None, path: str = "/settings") -> RedirectResponse:
    return _back(path, error, param="intervals_error", cookie=INTERVALS_COOKIE)


@app.get("/api/auth/intervals/start")
def intervals_start(request: Request, next: str | None = None) -> RedirectResponse:
    """Send the browser to intervals.icu to approve Talus for the signed-in account. It comes back
    to `next` (a path in Talus, default /settings), with ?intervals_error=... if it failed."""
    back = _safe_next(next or "/settings")
    if not intervals_auth.enabled():
        return _intervals_back("Connecting with intervals.icu isn't set up on this Talus. Use an API key.", back)
    accounts = get_accounts()
    account = accounts.session_account(request.cookies.get(COOKIE))
    if account is None:
        return _back("/login", "Sign in first.")
    state = secrets.token_urlsafe(24)
    sealed = accounts._cipher().encrypt(json.dumps({"state": state, "account": account.id, "next": back}).encode()).decode()
    response = RedirectResponse(intervals_auth.authorize_url(state), status_code=303)
    response.set_cookie(INTERVALS_COOKIE, sealed, max_age=OAUTH_TTL_S, httponly=True, samesite="lax", path="/",
                        secure=os.environ.get("TALUS_SECURE_COOKIE", "") == "1")
    return response


@app.get(intervals_auth.CALLBACK_PATH)
def intervals_callback(request: Request, code: str | None = None, state: str | None = None,
                       error: str | None = None) -> RedirectResponse:
    """intervals.icu sends the browser back here. The token replaces any saved API key."""
    accounts = get_accounts()
    try:
        flow = json.loads(accounts._cipher().decrypt(request.cookies.get(INTERVALS_COOKIE, "").encode(),
                                                     ttl=OAUTH_TTL_S))
    except Exception:
        return _intervals_back("Connecting intervals.icu timed out. Try again.")
    back = flow.get("next", "/settings")
    if error or not code:
        return _intervals_back("Connecting intervals.icu was cancelled.", back)
    if not state or not secrets.compare_digest(state, flow["state"]):
        return _intervals_back("The connection didn't match this browser. Try again.", back)
    current = accounts.session_account(request.cookies.get(COOKIE))
    if current is None or current.id != flow["account"]:
        return _back("/login", "Sign in first.")
    try:
        values = intervals_auth.exchange_code(code)
    except intervals_auth.IntervalsAuthError as e:
        return _intervals_back(str(e), back)
    except httpx.HTTPError:
        return _intervals_back("Couldn't reach intervals.icu. Try again.", back)
    accounts.set_credential(current.id, "intervals", "oauth", values)
    return _intervals_back(path=back)


@app.post("/api/account/google/unlink")
def google_unlink() -> dict:
    try:
        get_accounts().unlink_identity(current_account().id, "google")
    except AccountError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


# Admin page: only ADMIN_EMAIL's account. Everyone else gets 404, as if there were no such page.
# Actions are logged in accounts.db's admin_log. Accounts can't be deleted from here.

def _admin() -> tuple[Accounts, Account]:
    account = current_account()
    if not account.is_admin:
        raise HTTPException(404, "Not Found")
    return get_accounts(), account


def _target(accounts: Accounts, admin_account: Account, account_id: int) -> Account:
    target = accounts.get(account_id)
    if target is None:
        raise HTTPException(404, "No such account.")
    if target.id == admin_account.id:
        raise HTTPException(409, "That's your own account. Change it from Settings.")
    return target


@app.get("/api/admin")
def admin_overview() -> dict:
    accounts, _ = _admin()
    month = date.today().replace(day=1)
    rows = [admin.account_row(accounts, a, month) for a in accounts.all()]
    emails = {a["id"]: a["email"] for a in rows}
    log = [e | {"admin": emails.get(e["admin_id"]), "target": emails.get(e["target_id"])}
           for e in accounts.admin_log(30)]
    return {"accounts": rows, "month": month.isoformat(),
            "signup": {"open": _signup_open(accounts), "chosen": accounts.config("signup_open"),
                       "env": os.environ.get("TALUS_SIGNUP", "open").lower() != "closed"},
            "features": {k: bool(accounts.config(k)) for k in FEATURES},
            "system": admin.system(accounts), "log": log}


# Feature flags the admin turns on for everyone. All off by default.
FEATURES = ("show_ai_cost",)


class FeatureIn(BaseModel):
    on: bool


@app.put("/api/admin/features/{name}")
def admin_feature(name: str, body: FeatureIn) -> dict:
    accounts, me = _admin()
    if name not in FEATURES:
        raise HTTPException(404, "No such feature.")
    accounts.set_config(name, body.on)
    accounts.log_admin(me.id, "feature_on" if body.on else "feature_off", None, {"feature": name})
    return {name: body.on}


class SignupIn(BaseModel):
    open: bool


@app.put("/api/admin/signup")
def admin_signup(body: SignupIn) -> dict:
    accounts, me = _admin()
    accounts.set_config("signup_open", body.open)
    accounts.log_admin(me.id, "signup_open" if body.open else "signup_closed")
    return {"open": _signup_open(accounts)}


@app.post("/api/admin/accounts/{account_id}/{action}")
def admin_account_action(account_id: int, action: str) -> dict:
    """disable, enable, or sign-out (end every session of the account)."""
    accounts, me = _admin()
    if action not in ("disable", "enable", "sign-out"):
        raise HTTPException(404, "Not Found")
    target = _target(accounts, me, account_id)
    detail = None
    if action == "sign-out":
        detail = {"sessions": accounts.end_sessions(target.id)}
    else:
        accounts.set_disabled(target.id, action == "disable")
    accounts.log_admin(me.id, action, target.id, detail)
    return {"ok": True} | (detail or {})


class CeilingIn(BaseModel):
    # None goes back to the server's ceiling (TALUS_COACH_CAP_USD).
    ceiling_usd: float | None = Field(ge=0, le=1000)


@app.put("/api/admin/accounts/{account_id}/coach-ceiling")
def admin_coach_ceiling(account_id: int, body: CeilingIn) -> dict:
    accounts, me = _admin()
    target = _target(accounts, me, account_id)
    if target.is_owner:
        raise HTTPException(409, "The owner's coach has no ceiling.")
    accounts.set_coach_ceiling(target.id, body.ceiling_usd)
    accounts.log_admin(me.id, "coach_ceiling", target.id, {"from": target.coach_ceiling_usd, "to": body.ceiling_usd})
    return {"ok": True}
