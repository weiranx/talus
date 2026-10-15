"""HTTP API over the engine for the web app. Run: uv run talus serve

Every number here comes from the engine; the web app only formats it. All distances are
miles and all vert is feet.
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import zipfile
from collections import OrderedDict
import contextvars
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Literal

import httpx
import numpy as np
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import MODEL_VERSION, admin, chatgpt, cli, coach, explain, google_auth, interview, intervals_auth, ledger, naming, readiness_score, status, wellness, zones
from . import heatmap, terrain
from . import plan as planner
from . import schedule
from . import settings as app_settings
from .accounts import SESSION_DAYS, Account, AccountError, Accounts
from .config import data_dir, load_env
from .models import Thresholds
from .parse import parse
from .readiness import daily_call, readiness
from .route import route_numbers
from .score import MAX_DT_S, MOVING_MPS, _hiking, score
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
    """Every /api route except /api/auth/* and /api/health needs a signed-in session. The account is set for the
    request, and get_store() opens that account's folder, so no route can reach another
    account's data."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or not path.startswith("/api/") or path.startswith("/api/auth/")
                or path == "/api/health"):
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


def _names_on(store: Store) -> bool:
    return bool(store.settings().get("ai_names", True))


def _name(row, names: dict[int, dict], on: bool) -> dict:
    """The name shown for an activity: one typed in Talus, else the coach's when the device's name
    is generic (and AI names are on), else the device's."""
    n = names.get(row["id"]) or {}
    if n.get("custom_name"):
        return {"name": n["custom_name"], "source_name": row["name"], "name_by": "you"}
    if on and n.get("ai_name") and naming.is_generic(row["name"]):
        return {"name": n["ai_name"], "source_name": row["name"], "name_by": "ai"}
    return {"name": row["name"], "source_name": row["name"], "name_by": "source"}


def _activities(store: Store, since: date | None = None, limit: int | None = None) -> list[dict]:
    zm = _zone_model(store)
    names, on = store.activity_names(), _names_on(store)
    out = []
    for row in store.scored_activities(since and since.isoformat(), limit):
        m, hr_zone_minutes = _summary(row["summary"], zm)
        out.append({"id": row["id"], "day": row["start_time"][:10], "start_time": row["start_time"],
                    **_name(row, names, on), "sport": row["sport"], "source": row["source"],
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
    planned, ahead = _today_plan(store, day, th.aet)
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
                 "gain_ft": sum(a["gain_ft"] for a in week), "loss_ft": sum(a["loss_ft"] for a in week),
                 # Last week through the same weekday as today, so the week in progress compares like for like.
                 "last_week_to_date": _week_totals(last_28, (week_start - timedelta(weeks=1)).isoformat(),
                                                   (day - timedelta(weeks=1) + timedelta(days=1)).isoformat())},
        "ahead": ahead,
        "intensity_28d": _zone_share(last_28),
        "zones_28d": _display_share(last_28, zm),
        "zone_model": _zone_model_json(zm),
        "thresholds": _thresholds(store),
    }


TREND_DAYS = 28
HEALTH_HISTORY_DAYS = (30, 90, 180, 365)


@app.get("/api/wellness/history")
def wellness_history(days: int = 90) -> dict:
    """Every morning's HRV, resting HR and sleep over the last `days`, newest first, each read the
    way Today read it that morning (7-day HRV and resting HR against the 60-day baseline, sleep
    against the stated need), plus averages over the range for the header."""
    if days not in HEALTH_HISTORY_DAYS:
        raise HTTPException(400, f"days must be one of {', '.join(map(str, HEALTH_HISTORY_DAYS))}")
    store = get_store()
    day = date.today()
    start = day - timedelta(days=days - 1)
    need = wellness.sleep_need(store.settings())[0]
    nights = store.wellness(start - timedelta(days=wellness.BASELINE_DAYS - 1), day)
    sources = dict(store.db.execute("SELECT day, source FROM wellness WHERE day BETWEEN ? AND ?",
                                    (start.isoformat(), day.isoformat())).fetchall())
    rows = []
    for i in range(days):
        d = day - timedelta(days=i)
        n = nights.get(d)
        if not n or not any(n.get(k) for k in ("hrv", "resting_hr", "sleep_s")):
            continue
        w = wellness.evaluate(nights, d, need)
        rows.append({"day": d.isoformat(), "source": sources.get(d.isoformat()),
                     "hrv": n["hrv"] or None, "hrv_avg_7d": w["hrv"]["avg_7d"], "hrv_baseline": w["hrv"]["baseline"],
                     "hrv_status": w["hrv"]["status"],
                     "resting_hr": n["resting_hr"] or None, "resting_hr_baseline": w["resting_hr"]["baseline"],
                     "resting_hr_status": w["resting_hr"]["status"],
                     "sleep_h": n["sleep_s"] / 3600 if n["sleep_s"] else None, "sleep_status": w["sleep"]["status"]})

    def avg(key: str) -> float | None:
        vals = [r[key] for r in rows if r[key]]
        return wellness.mean(vals) if vals else None

    return {"days": days, "need_h": need, "nights": rows,
            "average": {"hrv": avg("hrv"), "resting_hr": avg("resting_hr"), "sleep_h": avg("sleep_h")}}


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
        **_ahead(store, day, hist["since"]),
    }


def _week_totals(acts: list[dict], start: str, end: str) -> dict:
    """What was done from `start` up to, not including, `end`: every activity, as the Activities page counts it."""
    got = [a for a in acts if start <= a["day"] < end]
    return {"start": start, "activities": len(got),
            **{k: sum(a[k] or 0 for a in got) for k in (
                "distance_mi", "gain_ft", "loss_ft", "effort_mi", "moving_time_s",
                "cardio_load", "climb_load", "descent_load")}}


def _ahead(store: Store, day: date, since: date | None) -> dict:
    """What's ahead on the Load page: `projection`, where the scheduled runs take each ledger, and
    `next_week`, how much the next 7 days can hold beside what's scheduled in them."""
    acts = _activities(store, day - timedelta(days=planner.CAPACITY_DAYS))
    items = _calendar(store, day, schedule.PROJECT_DAYS + 1, acts)
    done_today = any(a["day"] == day.isoformat() and planner.is_foot(a["sport"]) for a in acts)
    history = _plan_history(store, day)
    rates = planner.rates(acts, day)
    return {"projection": schedule.project(items, history, rates, day, done_today),
            "next_week": _next_week(items, history, rates, day + timedelta(days=1 if done_today else 0), since)}


# Each ledger's load in the units a run is planned in: hours, miles, feet up, feet down.
ROOM_UNITS = {"cardio": ("hours", "cardio_per_hour"), "distance": ("miles", None),
              "climb": ("gain_ft", "climb_per_ft"), "descent": ("loss_ft", "descent_per_ft")}


def _next_week(items: list[dict], history: list[ledger.LedgerDay], rates: dict, first: date,
               since: date | None) -> dict:
    """The 7 days from `first`: what's scheduled, and per ledger the total that holds your normal
    and the most before ramping too fast, spread evenly. A ledger still building its normal, or
    with none, has no room figures: any load would read as a ramp."""
    end = first + timedelta(days=7)
    ahead = [it for it in items if first.isoformat() <= it["day"] < end.isoformat() and it["status"] != "done"]
    minutes = [it["minutes"] for it in ahead]
    scheduled = {"runs": len(ahead), "miles": sum(it["miles"] for it in ahead),
                 "gain_ft": sum(it["gain_ft"] for it in ahead), "loss_ft": sum(it["loss_ft"] for it in ahead),
                 "hours": None if None in minutes else sum(minutes) / 60}
    room = {}
    for name in ledger.LEDGERS:
        days = [d for d in history if d.ledger == name and d.day < first]
        if not days or (since is not None and (days[-1].day - since).days + 1 < ledger.MIN_HISTORY_DAYS):
            continue
        r = ledger.week_room(days[-1].fitness, days[-1].fatigue)
        if r is None:
            continue
        unit, rate = ROOM_UNITS[name]
        per = rates[rate] if rate else 1.0
        room[name] = {"unit": unit, "hold": r["hold"] / per, "limit": r["limit"] / per}
    return {"start": first.isoformat(), "end": (end - timedelta(days=1)).isoformat(), "scheduled": scheduled, "room": room}


@app.get("/api/activities")
def activities(limit: int = 50) -> list[dict]:
    store = get_store()
    warm_details(store)
    return _activities(store, limit=limit)


@app.get("/api/heatmap")
def heatmap_routes() -> dict:
    """Every scored activity with its route as an encoded polyline (null without GPS), for the
    heatmap and its calendar. Lines not worked out yet are read in the background; `pending`
    counts them, and the page asks again until it is 0."""
    store = get_store()
    rows = store.scored_activities()
    tracks = heatmap.Tracks(store.dir)
    lines, missing = tracks.lines(rows)
    tracks.build(missing, lambda row: _streams(store, row))
    pending = {r["id"] for r in missing}
    return {"pending": len(pending),
            "activities": [{**{k: a[k] for k in ("id", "day", "start_time", "name", "sport", "distance_mi", "gain_ft",
                                                  "loss_ft", "effort_mi", "moving_time_s")},
                            "kind": heatmap.kind(a["sport"]), "line": lines.get(a["id"]),
                            "pending": a["id"] in pending}
                           for a in _activities(store)]}


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
                       "points": MAX_POINTS, "terrain": terrain.VERSION}, sort_keys=True)


def _detail_path(store: Store, row, key: str):
    """Named for its key, so whether an activity is ready is one stat, without reading it."""
    return store.dir / "cache" / f"activity-{row['id']}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def _detail_core(store: Store, row, th: Thresholds, zm: dict, remember: bool = True) -> dict:
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
        lock = _detail_locks.setdefault(mem_key[:2], threading.Lock())
    with lock:
        hit = _detail_from_disk_or_file(store, row, th, zm, key)
    if remember:
        with _details_guard:
            _details[mem_key] = hit
            while len(_details) > DETAILS_KEEP:
                _details.popitem(last=False)
    return hit


def _detail_from_disk_or_file(store: Store, row, th: Thresholds, zm: dict, key: str) -> dict:
    path = _detail_path(store, row, key)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    hit = _score_detail(store, row, th, zm)
    try:
        path.parent.mkdir(exist_ok=True)
        for old in path.parent.glob(f"activity-{row['id']}-*.json"):  # from older thresholds or zones
            old.unlink(missing_ok=True)
        _write_json(path, hit)
        _save_terrain(store, row, hit["terrain"])
    except (OSError, TypeError):
        pass  # a read-only or full disk only costs the next view a re-read
    return hit


def _write_json(path, data) -> None:
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


# Each activity's grade and durability numbers are also kept on their own, keyed on the file only,
# so comparing an activity with the athlete's earlier ones reads a few small files, and survives a
# change of thresholds or zones.
_terrains: dict[str, dict | None] = {}
_terrains_guard = threading.Lock()


def _terrain_path(store: Store, row):
    st = (store.dir / row["raw_path"]).stat()
    key = json.dumps([row["raw_path"], st.st_mtime_ns, st.st_size, row["raw_fmt"], row["sport"], terrain.VERSION])
    return store.dir / "cache" / f"terrain-{row['id']}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def _save_terrain(store: Store, row, data: dict | None) -> None:
    path = _terrain_path(store, row)
    if path.exists():
        return
    for old in path.parent.glob(f"terrain-{row['id']}-*.json"):
        old.unlink(missing_ok=True)
    _write_json(path, {"terrain": data})


def _terrain_of(store: Store, row) -> dict | None:
    """An activity's saved grade and durability numbers, or None until its file has been read
    (the background read-ahead gets to every activity)."""
    try:
        path = str(_terrain_path(store, row))
    except OSError:
        return None
    with _terrains_guard:
        if path in _terrains:
            return _terrains[path]
    try:
        data = json.loads(open(path).read())["terrain"]
    except (OSError, ValueError, KeyError):
        return None
    with _terrains_guard:
        _terrains[path] = data
    return data


def _terrain_history(store: Store, row) -> list[dict]:
    """Grade and durability numbers of the same kind of activity in the 90 days before this one."""
    start = row["start_time"]
    since = (date.fromisoformat(start[:10]) - timedelta(days=terrain.HISTORY_DAYS)).isoformat()
    k = heatmap.kind(row["sport"])
    return [t for r in store.scored_activities(since)
            if r["start_time"] < start and r["id"] != row["id"] and heatmap.kind(r["sport"]) == k
            and (t := _terrain_of(store, r))]


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

    raw_dt = np.diff(streams.time, prepend=streams.time[0])
    dt = np.minimum(raw_dt, MAX_DT_S)
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(raw_dt > 0, np.diff(streams.distance, prepend=streams.distance[0]) / raw_dt, 0.0)
    hiking = _hiking(streams, speed)
    moving = speed > MOVING_MPS
    segments = []
    for n, seg in enumerate(result.segments):
        sl = (streams.time >= seg.start_s) & (streams.time <= seg.end_s)
        z = None if split is None else {f"z{k}": float(np.sum(dt[sl & (split == k)]) / 60) for k in (1, 2, 3)}
        hz = None if zone is None else {f"z{k}": float(np.sum(dt[sl & (zone == k)]) / 60)
                                        for k in range(1, zm["zones"] + 1)}
        mt = float(np.sum(dt[sl & moving]))
        vert_m = seg.gain_m if seg.kind == "climb" else seg.loss_m if seg.kind == "descent" else 0.0
        seg_hr = None if hr is None or mt <= 0 else round(float(np.sum((hr * dt)[sl & moving]) / mt), 1)
        segments.append({"index": n, "kind": seg.kind, "distance_mi": mi(seg.distance_m),
                         "gain_ft": ft(seg.gain_m), "loss_ft": ft(seg.loss_m), "avg_grade": seg.avg_grade,
                         "grade_band": seg.grade_band, "hike_share": seg.hike_share,
                         "time_s": seg.end_s - seg.start_s, "start_s": seg.start_s, "end_s": seg.end_s,
                         "zone_minutes": z, "hr_zone_minutes": hz, "climb_load": seg.climb_load,
                         "descent_load": seg.descent_load, "avg_hr": seg_hr,
                         "vert_rate_ft_h": round(ft(vert_m) / mt * 3600) if mt >= 60 and vert_m else None})
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
        "terrain": terrain.analyze(streams, hiking),
        "streams": {"time_s": pick(streams.time, nd=0), "distance_mi": pick(streams.distance, mi, 3),
                    "altitude_ft": pick(streams.altitude, ft, 0), "heart_rate": pick(hr, nd=0),
                    "zone": None if zone is None else [int(v) for v in zone[idx]],
                    "lat": pick(streams.lat, nd=6), "lon": pick(streams.lon, nd=6)},
    }


# Folders whose activities are being read ahead, by a background thread each.
_warming: set[str] = set()
_warming_guard = threading.Lock()
_warmed_at: dict[str, float] = {}  # when each folder last finished with nothing left to read
REWARM_S = 300


def warm_details(store: Store, force: bool = False) -> None:
    """Read every activity's file ahead, newest first, in a low-priority background thread, so
    opening any activity finds it already scored. Each is read once and kept on disk; later warms
    only check that each one's file is there. New activities synced meanwhile are picked up by
    another pass. Page loads re-check every few minutes; a sync (`force`) re-checks at once."""
    folder = store.dir
    key = str(folder)
    with _warming_guard:
        if key in _warming or (not force and time.monotonic() - _warmed_at.get(key, -REWARM_S) < REWARM_S):
            return
        _warming.add(key)

    def run() -> None:
        try:
            try:  # the server's requests come first
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
            except (AttributeError, OSError):
                pass
            st = Store(folder)
            while True:
                th = st.thresholds()
                if th is None:
                    return
                zm = zones.model(th, st.settings().get("hr_zones"))
                read = 0
                for row in st.scored_activities():
                    started = time.monotonic()
                    try:
                        path = _detail_path(st, row, _detail_key(st, row, th, zm))
                        if path.exists():
                            continue
                        _detail_core(st, row, th, zm, remember=False)
                        read += path.exists()  # a disk that won't keep it ends the warm
                    except Exception:  # an unreadable file: its page says why when opened
                        pass
                    # Rest as long as the read took: at most half the CPU, so pages stay quick.
                    time.sleep(max(0.05, time.monotonic() - started))
                if not read:
                    _warmed_at[key] = time.monotonic()
                    return
        finally:
            with _warming_guard:
                _warming.discard(key)

    threading.Thread(target=run, name=f"warm {key}", daemon=True).start()


def read_ahead(store: Store) -> dict:
    """How far the background read of every activity (warm_details) has got: one stat each."""
    th = store.thresholds()
    rows = store.scored_activities()
    with _warming_guard:
        running = str(store.dir) in _warming
    if th is None:
        return {"state": "idle", "ready": 0, "total": len(rows), "running": running, "waiting_for": "thresholds"}
    zm = zones.model(th, store.settings().get("hr_zones"))
    ready = 0
    for row in rows:
        try:
            ready += _detail_path(store, row, _detail_key(store, row, th, zm)).exists()
        except OSError:  # file missing: its page says why when opened
            pass
    # Short of done and not running: the next page load (or this status check) carries on.
    state = "ok" if ready == len(rows) or running else "idle"
    return {"state": state, "ready": ready, "total": len(rows), "running": running, "waiting_for": None}


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
    core["terrain"] = terrain.compare(core.get("terrain"), _terrain_history(store, row))
    day = date.fromisoformat(row["start_time"][:10])
    before = {d.ledger: d.fitness for d in store.ledgers(day - timedelta(days=1))
              if d.day == day - timedelta(days=1)}
    six_weeks = [a for a in _activities(store, day - timedelta(days=42)) if a["day"] <= day.isoformat()]

    def usual(name: str, value: float | None) -> dict:
        others = [a[ledger.ACTIVITY_KEY[name]] or 0 for a in six_weeks if a["id"] != activity_id]
        return {"load": value,
                "times_usual_day": None if value is None else ledger.times_usual_day(value, before.get(name, 0.0)),
                "six_week_high": value is not None and bool(others) and value > max(others)}

    return {"id": activity_id, **_name(row, store.activity_names(), _names_on(store)), "day": day.isoformat(),
            "start_time": row["start_time"],
            "source": row["source"], "device_name": row["device_name"], **core,
            "loads": {name: usual(name, load_values[name]) for name in ledger.ACTIVITY_KEY}}


MODEL_KEYS = ("brief_model", "analysis_model")


def coach_models(accounts: Accounts | None = None) -> dict:
    """The coach's models: one choice for every account, made on the admin page. Before the admin
    first picks, the owner's old per-account picks carry over as that choice; with neither,
    TALUS_COACH_MODEL or the built-in defaults. plan_model builds and checks plans; until the
    admin picks one it is the analysis model."""
    accounts = accounts or get_accounts()
    chosen = accounts.config("coach_models")
    if chosen is None:
        owner = accounts.owner()
        saved = Store(accounts.user_dir(owner.id)).settings() if owner else {}
        chosen = {k: saved[k] for k in MODEL_KEYS if saved.get(k) in app_settings.MODELS}
        if chosen:
            accounts.set_config("coach_models", chosen)
    out = {k: chosen.get(k) if chosen.get(k) in app_settings.MODELS else coach.default_model(k.removesuffix("_model"))
           for k in MODEL_KEYS}
    return out | {"plan_model": chosen.get("plan_model") if chosen.get("plan_model") in app_settings.MODELS
                  else out["analysis_model"]}


def _provider_client(store: Store):
    if store.settings().get("ai_provider", "anthropic") == "chatgpt":
        accounts, account_id = get_accounts(), current_account().id
        connection = chatgpt.status(accounts, account_id)
        if not connection["available"] or not connection["sharing"] or not store.settings().get("chatgpt_model"):
            return None
        return chatgpt.Client(accounts, account_id)
    return coach_client()


def _coach_settings(store: Store) -> dict:
    saved = store.settings()
    cfg = {k: saved.get(k, v) for k, v in app_settings.defaults().items()} | coach_models()
    if saved.get("ai_provider") == "chatgpt":
        model = "chatgpt/" + (saved.get("chatgpt_model") or "unselected")
        cfg.update(brief_model=model, analysis_model=model, plan_model=model)
    return cfg


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
    cheap = mode in coach.FIELD_MODES or mode in coach.CHEAP_MODES
    model_id = (cfg["brief_model"] if cheap else cfg["plan_model"] if mode in coach.PLAN_MODES
                else cfg.get(f"{mode}_model", cfg["analysis_model"]))
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
    if not model_id.startswith("chatgpt/") and cap["limit"] is not None and cap["spent"] >= cap["limit"]:
        return {"source": "rules", "reply": fallback, "log_id": None, "feedback": None,
                "fallback_reason": "cap", "model": None, "cost": None}
    result = coach.run(mode, packet, fallback, _provider_client(store), model_id, style)
    log_id = None
    if result.fallback_reason != "no_key":
        log_id = store.log_coach(mode, subject, coach.PROMPT_VERSION, h, packet, result)
    return {"source": result.source, "reply": result.reply, "log_id": log_id, "feedback": None,
            "fallback_reason": result.fallback_reason, "model": result.model,
            "cost": _shown_cost(result.model, result.usage, cached=False)}


def _noted(store: Store, packet: dict) -> dict:
    """Every coach answer reads the athlete's training note, when they have written one."""
    if note := (store.settings().get("coach_note") or "").strip():
        packet["athlete_note"] = note
    return packet


@app.get("/api/coach/note")
def get_coach_note() -> dict:
    return {"note": get_store().settings().get("coach_note") or ""}


class NoteIn(BaseModel):
    note: str = Field(max_length=coach.NOTE_MAX)


@app.put("/api/coach/note")
def put_coach_note(body: NoteIn) -> dict:
    get_store().save_settings({"coach_note": body.note.strip()})
    return {"ok": True}


class NoteDraftIn(BaseModel):
    training_for: str = Field(default="", max_length=300)
    wants: str = Field(default="", max_length=300)
    avoid: str = Field(default="", max_length=300)


@app.post("/api/coach/note/draft")
def draft_coach_note(body: NoteDraftIn) -> dict:
    """The coach drafts the training note from three short answers and the athlete's recent
    training. The draft is returned to edit; nothing is saved until the athlete saves it."""
    answers = body.model_dump()
    if not any(v.strip() for v in answers.values()):
        raise HTTPException(422, "Answer at least one question.")
    store = get_store()
    day = date.today()
    packet = coach.note_packet(answers, _activities(store, day - timedelta(weeks=13)), day)
    out = _coach(store, "note", "note", packet, coach.rules_note(packet), False)
    return {"note": out["reply"]["note"].replace("**", "")[:coach.NOTE_MAX], "source": out["source"],
            "fallback_reason": out["fallback_reason"], "cost": out["cost"]}


@app.get("/api/coach/brief")
def coach_brief(refresh: bool = False) -> dict:
    t = today()
    store = get_store()
    day = date.fromisoformat(t["date"])
    acts = _activities(store, day - timedelta(weeks=coach.BRIEF_WEEKS))
    packet = _noted(store, coach.brief_packet(t, [a for a in acts if a["day"] >= (day - timedelta(days=13)).isoformat()], acts))
    fallback = coach.rules_brief(packet)
    out = _coach(store, "brief", t["date"], packet, fallback, refresh)
    if out["source"] == "coach":
        out["reply"] = coach.brief_reply(out["reply"], fallback, t["thresholds"]["aet"])
    return out


@app.get("/api/coach/load")
def coach_load(refresh: bool = False) -> dict:
    """The Load page's read: last week, the trend, what to watch and what the next 7 days should
    hold. On the brief's model, and reused all day while the numbers don't change."""
    t = today()
    store = get_store()
    day = date.fromisoformat(t["date"])
    acts = _activities(store, day - timedelta(weeks=8))
    ahead = _ahead(store, day, _history(store)["since"])
    packet = _noted(store, coach.load_packet(t, acts, ahead["next_week"]))
    return _coach(store, "load", t["date"], packet, coach.rules_load(packet), refresh)


@app.get("/api/coach/activity/{activity_id}")
def coach_activity(activity_id: int, refresh: bool = False) -> dict:
    a = activity(activity_id)
    store = get_store()
    day = date.today()
    now = {d.ledger: _ledger_json(d) for d in store.ledgers(day) if d.day == day}
    since = date.fromisoformat(a["day"]) - timedelta(days=max(coach.SIMILAR_DAYS, coach.BEST_DAYS))
    packet = _noted(store, coach.analysis_packet(a, _thresholds(store), now, day, _activities(store, since)))
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


def _accepted(store: Store, acts: list[dict] | None = None, day: date | None = None) -> dict | None:
    """The accepted plan as the athlete sees it: the week after each changed week adapted to the
    change (a day they edited, moved or took off, or the current week as it's run)."""
    p = store.plan("accepted")
    if p is None:
        return None
    day = day or date.today()
    acts = _activities(store, day - timedelta(days=day.weekday() + 7)) if acts is None else acts
    own = schedule.own_by_day(store.planned_runs(day - timedelta(days=day.weekday() + 7),
                                                 date.fromisoformat(p["objective"]["day"])))
    return planner.adapt(p, acts, day, own)


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
    return schedule.calendar(store.planned_runs(start, start + timedelta(days=days - 1)), _accepted(store, acts, day),
                             acts, start, days, day, planner.pace(acts, day)["min_per_effort_mi"],
                             th.aet if th else None, _zone_text(_zone_model(store)), own=_own(store, day),
                             ant=th.ant if th else None)


AHEAD_DAYS = 3  # the days after today that Today lists and the brief reads


def _today_plan(store: Store, day: date, aet: float) -> tuple[dict | None, dict]:
    """Today's scheduled run, and what's ahead of it: the plan's objective, this week's target
    with what's done and still scheduled, and each of the next AHEAD_DAYS days. One calendar read
    covers the week and the days after it."""
    acts = _activities(store, day - timedelta(days=planner.CAPACITY_DAYS))
    accepted = _accepted(store, acts, day)
    monday = day - timedelta(days=day.weekday())
    last = max(monday + timedelta(days=6), day + timedelta(days=AHEAD_DAYS))
    items = _calendar(store, monday, (last - monday).days + 1, acts)
    week = schedule.weeks(items, acts, accepted, monday, 7, day)[0]
    days = []
    for k in range(1, AHEAD_DAYS + 1):
        d = (day + timedelta(days=k)).isoformat()
        days.append({"day": d, "runs": [{f: it[f] for f in ("kind", "title", "detail", "source", "miles", "gain_ft",
                                                            "loss_ft", "minutes", "benefit", "workout")}
                                        for it in items if it["day"] == d]})
    objective = None
    if accepted is not None:
        o = accepted["objective"]
        goal = o.get("goal", "race")
        objective = {"name": o["name"], "goal": goal, "goal_text": planner.GOAL_KINDS.get(goal, goal),
                     "event": planner.is_event(o), "day": o["day"],
                     "days_to_go": (date.fromisoformat(o["day"]) - day).days}
    ahead = {"objective": objective, "has_plan": accepted is not None,
             "week": week if accepted is not None or week["ahead"]["miles"] else None, "days": days}
    return _planned_today(store, day, aet, items, acts, accepted), ahead


def _planned_today(store: Store, day: date, aet: float, items: list[dict], acts: list[dict],
                   accepted: dict | None) -> dict | None:
    """Today's scheduled run, the athlete's own or the plan's, unless it's already done."""
    items = [it for it in items if it["status"] == "today"]
    if not items:
        # A plan rest day is still the plan: on a go day the call follows it.
        rest = [s for s in _sessions(store, accepted, acts, day, aet, 1) if s["kind"] == "rest"]
        return None if not rest else {"title": "Rest", "detail": "Your plan has no run today. Rest, or an easy walk.",
                                      "source": "plan", "kind": "rest", "miles": 0.0, "gain_ft": 0.0, "loss_ft": 0.0}
    it = items[0]
    return {"title": it["title"], "detail": it["detail"], "source": it["source"], "kind": it["kind"],
            "miles": it["miles"], "gain_ft": it["gain_ft"], "loss_ft": it["loss_ft"], "minutes": it["minutes"],
            "benefit": it["benefit"], "workout": it["workout"]}


# The AI coach's build runs after the engine's draft is saved, so the request answers at once
# (the proxy in front of the engine drops requests that run past 100 s) and the draft updates in
# place when the coach is done. Tests run it inline.
AI_BUILD_INLINE = False
BUILD_STALE_S = 1800  # a build this old was lost (the engine restarted); the engine's draft stands


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
    except planner.PlanError as e:
        raise HTTPException(422, str(e))
    body["builder"] = "engine"
    ai = reason != "rolling" and _ai_builds(store)
    if ai:
        body["building"] = datetime.now().isoformat(timespec="seconds")
    body = _draft_extras(store, obj, acts, history, c, day, body)
    body["id"] = store.save_draft(obj.id, reason, body)
    if ai:
        engine_plan = {k: v for k, v in body.items() if k not in ("id", "building")}
        context = _athlete_context(store, acts, day)
        job = functools.partial(_ai_build_job, store.dir, body["id"], obj, acts, history, c, day, engine_plan, context)
        if AI_BUILD_INLINE:
            job()
        else:
            ctx = contextvars.copy_context()  # the account, for the coach's settings and spending cap
            threading.Thread(target=ctx.run, args=(job,), name=f"plan build {body['id']}", daemon=True).start()
    return body


def _athlete_context(store: Store, acts: list[dict], day: date) -> dict:
    """Everything known about the athlete for the coach that builds the plan, read here in the
    request so the build can run in the background: Today's readiness, loads and overnight
    trend, check-ins, recent days, their own scheduled runs and how the last plan went."""
    try:
        t = today()
    except HTTPException:
        t = None  # no thresholds yet
    checkins = store.checkins(day - timedelta(days=13), day)
    ahead = [{"day": r.day.isoformat(), "kind": r.kind, "distance_mi": round(r.miles, 1), "gain_ft": round(r.gain_ft),
              "notes": r.notes or None} for r in store.planned_runs(day, day + timedelta(days=180))]
    last = []
    accepted = store.plan("accepted")
    if accepted is not None:
        for w in accepted["weeks"]:
            start = date.fromisoformat(w["start"])
            if day - timedelta(weeks=6) <= start < day - timedelta(days=day.weekday()):
                a = planner.week_actual(acts, start)
                last.append({"week_start": w["start"], "focus": w["focus"],
                             "target": {"distance_mi": round(w["miles"], 1), "gain_ft": round(w["gain_ft"])},
                             "done": {"distance_mi": round(a["miles"], 1), "gain_ft": round(a["gain_ft"])}})
    return coach.athlete_context(t, acts, day, checkins, ahead, last)


def _plan_answers(store: Store) -> dict | None:
    """What the athlete told the coach when planning: every answer for the builder and checker,
    typed words included (older setups kept only the taps)."""
    saved = store.settings()
    return saved.get("plan_answers") or saved.get("plan_context")


def _ai_builds(store: Store) -> bool:
    """Whether the AI coach builds this account's drafts: on in Settings, the coach on, a key set."""
    return bool(store.settings().get("ai_plans", True) and _coach_settings(store)["coach_enabled"]
                and _provider_client(store) is not None)


def _draft_extras(store: Store, obj: planner.Objective, acts: list[dict], history: list, c: planner.Constraints,
                  day: date, body: dict) -> dict:
    body["options"] = planner.options(obj, acts, history, c, day, body)
    body["now"] = (_readiness_json(planner.readiness_now(obj, acts, _descent_fitness(history), day))
                   if planner.is_event(obj) else None)
    body["changes"] = planner.diff(store.plan("accepted"), body)
    return body


def _ai_build_job(folder, draft_id: int, obj: planner.Objective, acts: list[dict], history: list,
                  c: planner.Constraints, day: date, engine_plan: dict, context: dict | None = None) -> None:
    """Replace the saved draft with the coach's version, or mark the engine's as final. A draft
    accepted, declined or replaced meanwhile is left alone."""
    store = Store(folder)
    try:
        body = _ai_build(store, obj, acts, history, c, day, engine_plan, context)
        if body.get("builder") == "coach":
            body = _draft_extras(store, obj, acts, history, c, day, body)
    except Exception as e:  # noqa: BLE001 - any failure leaves the engine's draft standing
        body = engine_plan | {"builder_note": f"error: {type(e).__name__}"}
    store.replace_draft(draft_id, body)


def _building(p: dict) -> bool:
    started = p.get("building")
    if not started:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(started)).total_seconds() < BUILD_STALE_S
    except (TypeError, ValueError):
        return False


def _ai_build(store: Store, obj: planner.Objective, acts: list[dict], history: list, c: planner.Constraints,
              day: date, engine_plan: dict, context: dict | None = None) -> dict:
    """The AI coach builds the weeks from the engine's draft, the athlete's history, note,
    answers and everything else known about them (`context`); the engine then holds every week
    to its rules. The engine's draft stands when the coach is off, over its cap, or its plan
    doesn't line up."""
    packet = coach.build_packet(engine_plan, acts, day)
    _noted(store, packet)
    if answers := _plan_answers(store):
        packet["athlete_answers"] = answers
    if context:
        packet["athlete_context"] = context
    out = _coach(store, "build", f"build-{obj.id}", packet, {"approach": "", "weeks": []}, False)
    if out["source"] != "coach":
        return engine_plan | {"builder_note": out["fallback_reason"]}
    try:
        plan = planner.build(obj, acts, history, c, day, out["reply"]["weeks"])
    except (planner.PlanError, KeyError, TypeError, ValueError) as e:
        return engine_plan | {"builder_note": f"rejected: {e}"}
    approach = out["reply"]["approach"][:800]
    try:
        plan, check, check_cost = _ai_check(store, obj, acts, history, c, day, engine_plan, plan, approach, context)
    except Exception:  # noqa: BLE001 - a failed check leaves the coach's plan as the engine held it
        check, check_cost = None, None
    held = sum(1 for w in plan["weeks"] if any(n.startswith("The coach asked for more") for n in w["notes"]))
    return plan | {"builder": "coach", "approach": approach, "build_cost": _cost_sum(out["cost"], check_cost),
                   "held_weeks": held, "check": check}


def _ai_check(store: Store, obj: planner.Objective, acts: list[dict], history: list, c: planner.Constraints,
              day: date, engine_plan: dict, plan: dict, approach: str,
              context: dict | None = None) -> tuple[dict, dict | None, dict | None]:
    """A second read by the coach of its plan as the engine held it. When the check finds
    problems it sends a revised plan, which the engine builds and holds to its limits again; the
    revision stands unless the engine refuses it or it puts a reachable objective out of reach.
    Returns the plan, what the check found (None when it didn't run) and its cost."""
    packet = coach.check_packet(engine_plan, plan, approach, acts, day)
    _noted(store, packet)
    if answers := _plan_answers(store):
        packet["athlete_answers"] = answers
    if context:
        packet["athlete_context"] = context
    out = _coach(store, "check", f"check-{obj.id}", packet, {"ok": True, "summary": "", "issues": [], "weeks": []}, False)
    if out["source"] != "coach":
        return plan, None, None
    reply = out["reply"]
    check = {"ok": bool(reply["ok"]), "summary": reply["summary"].strip()[:600],
             "issues": [str(i).strip()[:240] for i in reply["issues"][:coach.CHECK_MAX_ISSUES] if str(i).strip()],
             "revised": False}
    if not check["ok"] and reply["weeks"]:
        try:
            revised = planner.build(obj, acts, history, c, day, reply["weeks"])
        except (planner.PlanError, KeyError, TypeError, ValueError):
            return plan, check | {"kept": "refused"}, out["cost"]
        if plan["reachable"] and not revised["reachable"]:
            return plan, check | {"kept": "out_of_reach"}, out["cost"]
        plan, check["revised"] = revised, True
    return plan, check, out["cost"]


def _cost_sum(a: dict | None, b: dict | None) -> dict | None:
    """Two calls' shown costs as one line."""
    if a is None or b is None:
        return a or b
    return a | {"usd": round(a["usd"] + b["usd"], 6), "input_tokens": a["input_tokens"] + b["input_tokens"],
                "output_tokens": a["output_tokens"] + b["output_tokens"], "cached": a.get("cached") and b.get("cached")}


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
                                  "back_to_back", "race_type", "goal", "target", "plan_type",
                                  "builder", "approach", "build_cost", "held_weeks", "check",
                                  "options", "now", "changes")} | {"reason_text": planner.REPLAN_TEXT.get(p["reason"], p["reason"]),
                                                                   "building": _building(p)}


def _with_own_runs(store: Store, sessions: list[dict], acts: list[dict]) -> list[dict]:
    """The plan's next 7 days, with any run the athlete scheduled on a day in place of the plan's."""
    if not sessions:
        return sessions
    start = date.fromisoformat(sessions[0]["day"])
    own = {it["day"]: it for it in _calendar(store, start, len(sessions), acts) if it["source"] == "manual"}
    out = []
    for s in sessions:
        it = own.get(s["day"])
        if it is not None and s.get("own") and it["kind"] in ("tempo", "intervals", "hills", "long") and not it["notes"]:
            # A workout moved or changed on the calendar: the plan counts it as that week's session
            # of its kind, with its steps. Any other run of your own reads as you wrote it.
            out.append(s | {"run_id": it["id"], "notes": it["notes"]})
            continue
        out.append(s if it is None else s | {k: it[k] for k in ("kind", "miles", "gain_ft", "loss_ft", "minutes", "title")}
                   | {"how": it["notes"] or "Your own run.", "detail": it["detail"], "steps": [], "own": True,
                      "run_id": it["id"], "notes": it["notes"]})
    return out


def _workout_picks(c: planner.Constraints, ptype: str) -> dict:
    """The workouts the plan rotates through as they stand: the athlete's picks, or the plan
    type's defaults until they pick."""
    d = planner.default_workouts(ptype)
    return {"workouts": c.workouts if c.workouts is not None else [c.workout] if c.workout in planner.WORKOUTS
            else d["workouts"],
            "hill_sessions": c.hill_sessions if c.hill_sessions is not None else d["hill_sessions"],
            "picked": c.workouts is not None or c.hill_sessions is not None}


@app.get("/api/plan")
def get_plan() -> dict:
    store = get_store()
    day = date.today()
    th = store.thresholds()
    obj = store.objective()
    c = _constraints(store)
    acts = _activities(store)
    accepted = _accepted(store, acts, day)
    out = {"date": day.isoformat(), "constraints": asdict(c), "objective": None, "readiness": None,
           "accepted": None, "draft": None, "sessions": [], "review": None,
           "fitness_only": planner.FITNESS_ONLY, "thresholds": _thresholds(store),
           "workout_picks": _workout_picks(c, planner.plan_type(obj) if obj else "base")}
    if obj is None:
        return out
    if obj.goal == "rolling" and _roll(store, obj, day):
        obj, accepted = store.objective(), _accepted(store, acts, day)
    history = _plan_history(store, day)
    readiness = None
    if planner.is_event(obj):
        now = planner.readiness_now(obj, acts, _descent_fitness(history), day)
        readiness = {**_readiness_json(now), "demand": now["demand"], "capacity": now["capacity"], "pace": now["pace"]}
    draft = store.plan("draft")
    return out | {
        "objective": obj.json() | {"days_to_go": (obj.day - day).days, "event": planner.is_event(obj),
                                   "goal_text": planner.GOAL_KINDS.get(obj.goal, obj.goal)},
        "readiness": readiness,
        "accepted": _plan_json(accepted), "draft": _plan_json(draft),
        "sessions": _with_own_runs(store, _sessions(store, accepted, acts, day, th.aet if th else 150), acts),
        "review": _last_week_review(store, accepted, acts, day) if accepted else None,
        "this_week": _this_week(store, accepted, acts, day),
        "plan_type": planner.plan_type_text(obj),
        "actuals": {} if accepted is None else {
            w["start"]: {k: a[k] for k in ("miles", "gain_ft", "loss_ft", "below_aet_pct")}
            for w in accepted["weeks"] if w["start"] <= day.isoformat()
            for a in [planner.week_actual(acts, date.fromisoformat(w["start"]))]},
    }


@app.get("/api/plan/week")
def get_plan_week(start: date, draft: bool = False) -> dict:
    """One week of the plan, day by day, each with its session's detail: what was done on the
    days gone, then the plan's sessions around the athlete's own runs and rest days. A week
    further out than the calendar's dated days is the shape it would take from its targets."""
    store = get_store()
    day = date.today()
    start = start - timedelta(days=start.weekday())
    acts = _activities(store, start - timedelta(days=7))
    p = store.plan("draft") if draft else _accepted(store, None, day)
    if p is None:
        raise HTTPException(404, "No plan yet.")
    w = planner.plan_for_day(p, start)
    if w is None:
        raise HTTPException(404, "That week isn't in the plan.")
    own = {} if draft else schedule.own_by_day(store.planned_runs(start, start + timedelta(days=6)))
    th = store.thresholds()
    aet, ant = (th.aet, th.ant) if th else (150, None)
    name = p["objective"]["name"]
    runs = {} if draft else {r.day.isoformat(): r for r in store.planned_runs(start, start + timedelta(days=6))}
    days = []
    for s in planner.week_sessions(p, acts, day, start, own):
        if s["kind"] == "past":
            did = [a for a in acts if a["day"] == s["day"] and planner.is_foot(a["sport"])]
            days.append(s | {"title": "Done" if did else "Rest", "steps": [], "how": "", "benefit": None,
                             "activities": [{k: a[k] for k in ("id", "name", "distance_mi", "gain_ft", "loss_ft",
                                                               "moving_time_s")} for a in did]})
            continue
        text = planner.session_text(s, aet, name, ant)
        own_run = runs.get(s["day"]) if s.get("own") else None
        days.append(s | text | {"run_id": own_run.id if own_run else None,
                                "notes": own_run.notes if own_run else ""})
    dated = start < day + timedelta(days=schedule.plan_days(day))
    return {"week": w, "days": days, "dated": dated, "date": day.isoformat(), "objective": name,
            "plan_type": p.get("plan_type")}


def _roll(store: Store, obj: planner.Objective, day: date) -> bool:
    """An ongoing plan is redrawn from the latest training at the start of each week, and stays
    accepted: it always looks ROLLING_WEEKS ahead. True when it was redrawn."""
    accepted = store.plan("accepted")
    monday = day - timedelta(days=day.weekday())
    if accepted is None or accepted["built_on"] >= monday.isoformat() or store.plan("draft") is not None:
        return False
    obj.day = monday + timedelta(weeks=planner.ROLLING_WEEKS, days=-1)
    store.update_objective(obj)
    try:
        _build_draft(store, obj, "rolling")
    except HTTPException:
        return False
    draft = store.plan("draft")
    return draft is not None and store.decide_draft(draft["id"], True)


def _this_week(store: Store, accepted: dict | None, acts: list[dict], day: date) -> dict:
    """This week's target, done and still scheduled, as the calendar counts them."""
    start = day - timedelta(days=day.weekday())
    return schedule.weeks(_calendar(store, start, 7, acts), acts, accepted, start, 7, day)[0]


class ObjectiveIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    day: date
    # A race or trip's own numbers; a block's weekly targets; a yearly target's totals.
    distance_mi: float = Field(ge=0, le=20000)
    gain_ft: float = Field(ge=0, le=5_000_000)
    loss_ft: float = Field(ge=0, le=5_000_000)
    longest_climb_ft: float | None = Field(default=None, ge=0, le=30000)
    longest_descent_ft: float | None = Field(default=None, ge=0, le=30000)
    high_point_ft: float | None = Field(default=None, ge=-1500, le=30000)
    source: str = Field(default="manual", pattern="^(manual|gpx)$")
    # Left out (as iOS does), an edit keeps the current goal and a new objective is a race.
    goal: str | None = Field(default=None, pattern="^(race|trip|base|maintain|comeback|faster|target|rolling)$")
    new: bool = False  # true replaces the objective; false edits the active one


@app.put("/api/plan/objective")
def put_objective(body: ObjectiveIn) -> dict:
    """Set the objective and draft a plan for it. Editing the active objective drafts a new plan
    shown against the accepted one; a new objective starts over."""
    if body.day <= date.today():
        raise HTTPException(422, "Pick an objective date after today.")
    store = get_store()
    current = store.objective()
    if body.goal is None:
        body.goal = current.goal if current is not None and not body.new else "race"
    if body.goal in planner.EVENT_GOALS and not 0 < body.distance_mi <= 300:
        raise HTTPException(422, "A race or objective is more than 0 and at most 300 mi.")
    if body.goal != "target" and (body.gain_ft > 80000 or body.loss_ft > 80000 or body.distance_mi > 300):
        raise HTTPException(422, "That's more than 300 mi or 80,000 ft; only a yearly target goes past that.")
    if body.goal == "target" and not (body.distance_mi or body.gain_ft):
        raise HTTPException(422, "A yearly target needs miles, feet of climbing, or both.")
    fields = body.model_dump(exclude={"new"})
    fields["name"] = fields["name"].strip()
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
    weekday_min: int = Field(ge=15, le=planner.MAX_WEEKDAY_MIN)
    weekend_min: int = Field(ge=15, le=planner.MAX_WEEKEND_MIN)
    long_day: int = Field(ge=0, le=6)
    notes: str = Field(default="", max_length=500)
    back_to_back: bool | None = None  # left out: keep the current setting
    workout: str | None = Field(default=None, pattern="^(none|track|tempo|hill_repeats|race_pace|threshold|fartlek|progression)$")  # left out: keep
    # The workouts to rotate through, by key; left out: keep. An empty list: none of that kind.
    workouts: list[str] | None = Field(default=None, max_length=len(planner.WORKOUTS))
    hill_sessions: list[str] | None = Field(default=None, max_length=len(planner.HILL_SESSIONS))
    strides: bool | None = None
    strength: bool | None = None


@app.put("/api/plan/constraints")
def put_constraints(body: ConstraintsIn) -> dict:
    """Training days and time limits. With an objective set, a new plan is drafted from them."""
    if any(d < 0 or d > 6 for d in body.days):
        raise HTTPException(422, "Days are 0 (Monday) to 6 (Sunday).")
    if body.long_day not in body.days:
        raise HTTPException(422, "Your long day has to be one of your training days.")
    store = get_store()
    now = _constraints(store)
    if any(w not in planner.WORKOUTS for w in body.workouts or []) or any(
            h not in planner.HILL_SESSIONS for h in body.hill_sessions or []):
        raise HTTPException(422, "Unknown workout.")
    keep = {"adjust": now.adjust} | {k: getattr(now, k) for k in ("back_to_back", "workout", "workouts", "hill_sessions",
                                                                  "strides", "strength") if getattr(body, k) is None}
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


def _talk_caught_up(store: Store, st: dict) -> bool:
    """Once the coach's build of the draft finishes, say so in the conversation. True when the
    conversation changed and needs saving."""
    draft_id = st.get("building")
    if not draft_id:
        return False
    draft = store.plan("draft")
    if draft is not None and draft["id"] == draft_id and _building(draft):
        return False
    st.pop("building")
    if draft is not None and draft["id"] == draft_id and st.get("step") == "draft":
        text = interview.built_text(draft, st.get("mode", "setup"), st.get("answers"))
        st["messages"] = (st["messages"] + [interview.coach(text, "draft", [interview.CHANGE_GROUP],
                                                            actions=_draft_actions(store))
                                            | ({"cost": draft["build_cost"]} if draft.get("build_cost") else {})])[-TALK_KEEP:]
    return True


def _draft_actions(store: Store) -> list[dict]:
    actions = [{"id": "accept", "label": "Accept plan"}]
    if store.plan("accepted") is not None:
        actions.append({"id": "keep", "label": "Keep my current plan"})
    return actions


def _talk_json(store: Store, st: dict) -> dict:
    cfg = _coach_settings(store)
    return {"messages": st["messages"], "step": st["step"], "mode": st.get("mode", "setup"),
            "building": bool(st.get("building")),
            "reads_with_ai": bool(cfg["coach_enabled"] and _provider_client(store) is not None)}


def _talk_read(store: Store, mode: str, text: str, packet: dict, fallback: dict) -> tuple[dict, dict | None]:
    """Typed words read into fields by the cheaper model, or by the rules without it."""
    out = _coach(store, mode, mode, _noted(store, packet), fallback, False)
    return out["reply"], ({"cost": out["cost"]} if out["source"] == "coach" else None)


def _race_lookup(store: Store, text: str, day: date) -> tuple[dict, dict | None]:
    """A named race's date, distance and vert from the web, on the analysis model. Empty when
    the coach is off or over its cap, there's no key, or nothing usable was found."""
    cfg = _coach_settings(store)
    client = _provider_client(store)
    if isinstance(client, chatgpt.Client):
        return {}, None  # Ask for race details; Claude web-search tools are provider-specific.
    cap = _coach_cap(store)
    if not cfg["coach_enabled"] or client is None or (cap["limit"] is not None and cap["spent"] >= cap["limit"]):
        return {}, None
    model_id = cfg["analysis_model"]
    try:
        note = (store.settings().get("coach_note") or "").strip()
        found, model, usage = coach.lookup_race(client, text, day.isoformat(), model_id, note)
    except Exception as e:  # noqa: BLE001 - a failed lookup only means asking for the numbers
        import anthropic
        if not isinstance(e, (anthropic.APIError, ValueError)):
            raise
        return {}, None
    packet = _noted(store, {"today": day.isoformat(), "text": text})
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
    if o.get("looked_up") == "block" and body.text:
        o = {}  # typed a race instead of the offered block
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
    if o.get("looked_up") == "block" and o.get("confirmed") and not body.text:
        # A goal with no race day, confirmed: on to the training days (no "goal for the day" to ask).
        st["objective"] = o
        st["step"] = "days"
        return [interview.coach(f"Set: {interview.block_line(o)}. {interview.type_line(o)}"),
                interview.ask("days", st["defaults"])]
    chip = interview.GOAL_CHIPS.get(str((body.answers or {}).get("goal_kind") or ""))
    kind = chip or (interview.goal_kind(body.text) if body.text else None)
    if kind and kind != "target" and body.text and interview.rules_objective(body.text, day).get("distance_mi"):
        kind = None  # a race with its numbers, like "get faster for the Baldy 50k on Jan 2, 31 mi"
    if not kind and o.get("goal") == "target" and body.text and interview.year_totals(body.text):
        kind = "target"
    if kind:
        totals = interview.year_totals(body.text or "") if kind == "target" else None
        if kind == "target" and not totals:
            st["objective"] = {"goal": "target"}
            return [interview.coach("How much this year? Like \"1,000,000 ft\" or \"2,000 mi\", or both.", "objective")]
        # The whole year for a yearly target's "done so far", and four months for the baseline.
        acts = _activities(store, min(day - timedelta(days=120), date(day.year, 1, 1)))
        st["objective"] = interview.block_objective(kind, acts, day, totals)
        return [interview.coach(interview.block_text(st["objective"], acts, day), "objective",
                                [{"key": "confirm", "options": ["Use this"], "value": None}])]
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
    if not need and o.get("looked_up") and o["looked_up"] != "block" and not o.get("confirmed"):
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
    if not planner.is_event(o):  # kept a goal with no race day
        st["step"] = "days"
        return [interview.coach(f"Set: {interview.block_line(o)}. {interview.type_line(o)}"),
                interview.ask("days", st["defaults"])]
    st["step"] = "goal"
    return [interview.coach(f"Training for {interview.objective_line(o)}. {interview.type_line(o)}") | (meta or {}),
            interview.ask("goal")]


def _ptype(o: dict) -> str:
    """The plan type of an objective in the conversation, which may not say its goal yet."""
    return planner.plan_type({"goal": "race"} | {k: v for k, v in o.items() if v is not None})


def _talk_ask(store: Store, st: dict, step: str) -> dict:
    """The question for a step. The workouts question starts from the plan type's usual picks
    (or the athlete's own); the last one sums up what the coach will build."""
    if step == "workouts":
        o = st["objective"] or (store.objective().json() if store.objective() else {"goal": "base"})
        defaults = interview.workout_defaults(asdict(_constraints(store)) if st.get("changing") else {}, _ptype(o))
        return interview.ask_workouts(planner.plan_type_text({"goal": "race"} | o), defaults)
    if step == "confirm":
        return interview.ask_confirm({"goal": "race"} | st["objective"], st["answers"])
    return interview.ask(step, st["defaults"])


def _talk_apply(store: Store, st: dict, step: str, got: dict, day: date) -> list[dict]:
    """A quick change after a draft: the answer to one setup question, made to the plan's inputs,
    then a new draft."""
    st.pop("changing", None)
    st["step"] = "draft"
    obj = store.objective()
    if obj is None:
        st["step"] = "done"
        return [interview.coach("There's no plan to change. Start a new one from the Plan page.")]
    c = _constraints(store)
    if step == "days":
        c.days = sorted(interview.DAY_SHORT.index(d) for d in got["days"])
        c.long_day = interview.DAY_SHORT.index(got["long_day"])
        done = [f"training {', '.join(got['days'])} with the long day on {planner.DAY_NAMES[c.long_day]}"]
    elif step == "time":
        c.weekday_min, c.weekend_min = got["weekday_min"], got["weekend_min"]
        done = [f"weekdays up to {interview._time(c.weekday_min)} and weekend days up to {interview._time(c.weekend_min)}"]
    else:
        for k, v in interview.workout_constraints(got).items():
            setattr(c, k, v)
        picked = [*got.get("speed", []), *got.get("hills", []), *got.get("extras", [])]
        done = ["workouts set to " + (interview._join_words([p.lower() for p in picked]) if picked else "easy running only")]
        answers = store.settings().get("plan_answers") or {}
        store.save_settings({"plan_answers": answers | {k: got[k] for k in ("speed", "hills", "extras") if k in got}})
    before = store.plan("draft") or store.plan("accepted")
    store.save_settings({"plan_constraints": asdict(planner.Constraints.from_json(asdict(c)))})
    try:
        plan = _build_draft(store, obj, "constraints")
    except HTTPException as e:
        return [interview.coach(str(e.detail), "draft", [interview.CHANGE_GROUP])]
    if plan.get("building"):
        st["building"] = plan["id"]
        return [interview.coach(interview.change_text(done, [], None) + "\n" + interview.BUILDING_TEXT, "draft")]
    return [interview.coach(interview.change_text(done, [], planner.diff(before, plan)), "draft", [interview.CHANGE_GROUP],
                            actions=_draft_actions(store))]


def _talk_finish(store: Store, st: dict, day: date) -> list[dict]:
    """Save the answers as the objective and training days, then draft the plan."""
    a, o = st["answers"], st["objective"]
    c = _constraints(store)
    c.days = sorted(interview.DAY_SHORT.index(d) for d in a["days"])
    c.long_day = interview.DAY_SHORT.index(a["long_day"])
    c.weekday_min, c.weekend_min = a["weekday_min"], a["weekend_min"]
    c.notes = interview.notes(a)
    c.adjust = interview.lighter_start(day) if a.get("feel") == "Tired lately" else []
    if any(k in a for k in ("speed", "hills", "extras")):
        for k, v in interview.workout_constraints(a).items():
            setattr(c, k, v)
    if a.get("wants"):
        # What else they want, in their words: days, time limits and workouts it names change the
        # plan's inputs now; all of it goes to the coach that builds the plan.
        edits = [e for e in interview.rules_edits(a["wants"], day) if e["kind"] not in ("week", "rest_day")]
        c = planner.Constraints.from_json(interview.apply_edits(edits, asdict(c), [], _ptype(o))[0])
    store.save_settings({"plan_constraints": asdict(c), "plan_context": interview.context(a),
                         "plan_answers": interview.full_context(a)})
    current = store.objective()
    goal = o.get("goal") or ("trip" if interview.TRIP.search(o.get("name") or "") else "race")
    fields = {k: o.get(k) for k in ("name", "distance_mi", "gain_ft", "loss_ft", "longest_climb_ft",
                                    "longest_descent_ft", "high_point_ft")} | {
        "day": date.fromisoformat(o["day"]), "source": o.get("source", "manual"), "goal": goal}
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
    if plan.get("building"):
        # The AI coach's plan is what the athlete sees; the engine's draft only if the coach fails.
        st["building"] = plan["id"]
        return [interview.coach("Your answers are in. " + interview.BUILDING_TEXT, "draft")]
    return [interview.coach(interview.draft_summary(plan, obj.name, c.long_day, a), "draft", [interview.CHANGE_GROUP],
                            actions=_draft_actions(store))]


def _talk_change(store: Store, st: dict, text: str, day: date) -> list[dict]:
    obj = store.objective()
    before = store.plan("draft") or store.plan("accepted")
    if obj is None or before is None:
        st["step"] = "done"
        return [interview.coach("There's no plan to change. Start a new one from the Plan page.")]
    if interview.is_question(text) and not interview.rules_edits(text, day):
        return [_talk_question(store, text, day, obj, before)]
    c = _constraints(store)
    # Week 1 is this week, even in a plan accepted weeks ago.
    ahead = [w for w in before["weeks"] if w["start"] >= (day - timedelta(days=day.weekday())).isoformat()]
    packet = {"today": day.isoformat(), "text": text, "training_days": c.days, "long_day": c.long_day,
              "weeks_in_plan": len(ahead)}
    read, meta = _talk_read(store, "edit", text, packet, {"edits": interview.rules_edits(text, day)})
    edits = read["edits"] if meta is None else interview.from_edits(read["edits"])
    rest_note = _rest_days(store, [e for e in edits if e["kind"] == "rest_day"], day)
    edits = [e for e in edits if e["kind"] != "rest_day"]
    if rest_note and not edits:
        return [interview.coach(rest_note, "draft", [interview.CHANGE_GROUP],
                                actions=_draft_actions(store) if store.plan("draft") else []) | (meta or {})]
    # The rules can't tell "What pace should my tempo runs be?" from a change; only the coach can.
    if interview.is_question(text) and (not edits or meta is None):
        return [_talk_question(store, text, day, obj, before)]
    if not edits:
        asked = read.get("cannot") if meta is not None and isinstance(read.get("cannot"), str) else None
        return [interview.coach(interview.cant_text(asked), "draft", [interview.CHANGE_GROUP]) | (meta or {})]
    new_c, done, skipped = interview.apply_edits(edits, asdict(c), ahead, planner.plan_type(obj))
    if rest_note:
        done.insert(0, rest_note.removeprefix("Done: ").rstrip("."))
    if not done:
        return [interview.coach(interview.change_text(done, skipped, []) + "\n" + interview.EDIT_HELP, "draft",
                                [interview.CHANGE_GROUP]) | (meta or {})]
    store.save_settings({"plan_constraints": asdict(planner.Constraints.from_json(new_c))})
    try:
        plan = _build_draft(store, obj, "constraints")
    except HTTPException as e:
        return [interview.coach(str(e.detail), "draft") | (meta or {})]
    st["step"] = "draft"
    if plan.get("building"):
        st["building"] = plan["id"]
        return [interview.coach(interview.change_text(done, skipped, None) + "\n" + interview.BUILDING_TEXT, "draft")
                | (meta or {})]
    return [interview.coach(interview.change_text(done, skipped, planner.diff(before, plan)), "draft",
                            [interview.CHANGE_GROUP], actions=_draft_actions(store)) | (meta or {})]


def _rest_days(store: Store, edits: list[dict], day: date) -> str | None:
    """One-off days off asked for in the plan chat, put on the calendar as rest days: the plan
    works the rest of that week around them and eases the next. What was done, in words."""
    days = sorted({e["date"] for e in edits if e["date"] >= day.isoformat()})
    if not days:
        return None
    have = {r.day.isoformat() for r in store.planned_runs(date.fromisoformat(days[0]), date.fromisoformat(days[-1]))
            if r.kind == "rest"}
    for d in days:
        if d not in have:
            store.save_planned_run(schedule.PlannedRun(date.fromisoformat(d), "rest", 0.0, notes="Asked for in the plan chat"))
    names = [f"{planner.DAY_NAMES[date.fromisoformat(d).weekday()]}, {date.fromisoformat(d):%b} {date.fromisoformat(d).day}"
             for d in days]
    return (f"Done: {interview._join_words(names)} {'is a rest day' if len(names) == 1 else 'are rest days'}. The rest "
            "of that week moves around it and the week after adapts. It's on your calendar; delete it there to undo.")


def _talk_question(store: Store, text: str, day: date, obj: planner.Objective, plan: dict) -> dict:
    """A question in the plan chat gets the coach chat's answer, read against the plan being
    talked about. It isn't added to the Coach chat history and proposes no actions. Without the
    coach, a finish-time question gets the engine's estimate."""
    name = plan["objective"]["name"]
    fallback = interview.finish_text(plan, name) if interview.FINISH.search(text) else (
        "I can't answer that here without the AI coach. " + interview.EDIT_HELP)
    try:
        t = today()
    except HTTPException:
        t = None
    acts = _activities(store)
    now = (_readiness_json(planner.readiness_now(obj, acts, _descent_fitness(_plan_history(store, day)), day))
           if planner.is_event(obj) else None)
    packet = coach.chat_packet(text, [], t, plan, now, [], [a for a in acts if a["day"] >= (day - timedelta(days=13)).isoformat()],
                               day, acts)
    _noted(store, packet)
    out = _coach(store, "chat", "chat", packet, {"answer": fallback, "used": [], "actions": []}, False)
    answer = out["reply"]["answer"] if out["source"] == "coach" else fallback
    return interview.coach(answer, "draft") | ({"cost": out["cost"]} if out["source"] == "coach" else {})


@app.get("/api/plan/talk")
def get_talk(mode: str | None = None) -> dict:
    if mode not in (None, "setup", "change"):
        raise HTTPException(422, "Mode is setup or change.")
    store = get_store()
    st = _talk_state(store, mode)
    if _talk_caught_up(store, st):
        store.save_settings({TALK_KEY: st})
    return _talk_json(store, st)


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
    _talk_caught_up(store, st)
    step = st["step"]
    text = (body.text or "").strip()
    body.text = text or None
    # An empty list is an answer too: no workouts of that kind.
    answers = {k: v for k, v in (body.answers or {}).items() if v not in (None, "")}
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
        elif body.action == "accept" and _building(draft):
            reply = [interview.coach("The AI coach is still drafting this plan. It shows up here when it's ready.", "draft")]
            st["messages"] = (st["messages"] + reply)[-TALK_KEEP:]
            store.save_settings({TALK_KEY: st})
            return _talk_json(store, st)
        else:
            store.decide_draft(draft["id"], body.action == "accept")
            reply = [interview.coach("Done. Your plan is on the calendar, and you can change it here any time."
                                     if body.action == "accept" else "Kept your current plan.")]
        st["step"] = "done"
        st.pop("building", None)  # decided mid-build: nothing left to wait for
    elif body.action == "retry" or step == "stuck":
        reply = _talk_finish(store, st, day)
    elif step == "objective":
        reply = _talk_objective(store, st, body, day)
    elif step in interview.STEPS:
        got, problem = interview.chip_answers(step, answers, text, st["defaults"])
        if problem:
            reply = [interview.coach(problem, step, [g | {"value": answers.get(g["key"]) or st["defaults"].get(g["key"])}
                                                     for g in interview.QUESTIONS[step]["groups"]])]
        elif st.get("changing"):
            reply = _talk_apply(store, st, step, got, day)
        else:
            st["answers"] |= got
            nxt = interview.next_step(step)
            if nxt is None:
                reply = _talk_finish(store, st, day)
            else:
                st["step"] = nxt
                reply = [_talk_ask(store, st, nxt)]
    elif answers.get("change_topic") in interview.CHANGE_TOPICS and not text:
        # A quick change: ask that setup question again, starting from the plan's current inputs.
        topic = interview.CHANGE_TOPICS[str(answers["change_topic"])]
        st["changing"], st["step"] = topic, topic
        st["defaults"] = interview.days_defaults(asdict(_constraints(store)))
        reply = [_talk_ask(store, st, topic)]
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
    draft = get_store().plan("draft")
    if decision == "accept" and draft is not None and draft["id"] == plan_id and _building(draft):
        raise HTTPException(409, "The AI coach is still drafting this plan. Accept it once it's ready.")
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
    _noted(store, packet)
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
    _noted(store, packet)
    return _coach(store, "review", review["start"], packet, coach.rules_review(packet), refresh) | {"review": review}


@app.get("/api/coach/fit")
def coach_fit(refresh: bool = False) -> dict:
    """The latest training read against the accepted plan: did it do the planned session's job,
    where the week stands and what to do with the rest of it. Warmed after every sync."""
    store = get_store()
    day = date.today()
    accepted = store.plan("accepted")
    if accepted is None:
        raise HTTPException(404, "No accepted plan yet.")
    acts = _activities(store, day - timedelta(days=planner.CAPACITY_DAYS))
    start = day - timedelta(days=coach.FIT_DAYS - 1)
    items = _calendar(store, start, coach.FIT_DAYS, acts)
    recent = [a for a in acts if a["day"] >= start.isoformat() and planner.is_foot(a["sport"])]
    if not recent and not any(it["status"] == "missed" for it in items):
        raise HTTPException(404, "Nothing in the last few days to read against the plan.")
    flags = sorted({f for d in store.ledgers(day) if d.day == day and d.ledger in planner.PLAN_LEDGERS
                    for f in d.flags if f in ("ramp", "overreach")})
    replan = _last_week_review(store, accepted, acts, day)["replan_text"]
    packet = _noted(store, coach.fit_packet(items, recent, _this_week(store, accepted, acts, day),
                                            coach._objective(accepted, day), flags, replan, day))
    subject = max([a["day"] for a in recent], default=day.isoformat())
    return _coach(store, "fit", subject, packet, coach.rules_fit(packet), refresh) | {"day": subject}


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
            "weeks": schedule.weeks(items, acts, _accepted(store, acts, day), start, days, day),
            "warnings": _ahead(store, day, None)["projection"]["warnings"],
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
    if body.kind == "rest":  # a day off: nothing on it
        return schedule.PlannedRun(body.day, "rest", 0.0, 0.0, 0.0, None, None, body.notes.strip(), run_id)
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
    return {"messages": _chat_costs(_with_actions(get_store().chat()))}


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
    accepted = _accepted(store)
    obj = store.objective()
    acts = _activities(store)
    now = None
    if obj is not None and planner.is_event(obj):
        now = _readiness_json(planner.readiness_now(obj, acts, _descent_fitness(_plan_history(store, day)), day))
    plan_ctx = accepted if accepted is not None and obj is not None else None
    sessions = _with_own_runs(store, _sessions(store, plan_ctx, acts, day, t["thresholds"]["aet"] if t else 150), acts)
    packet = coach.chat_packet(question, history, t, plan_ctx, now if plan_ctx else None, sessions,
                               [a for a in acts if a["day"] >= (day - timedelta(days=13)).isoformat()], day, acts)
    _noted(store, packet)
    packet |= _action_context(store, day)
    store.add_chat("athlete", question)
    out = _coach(store, "chat", "chat", packet, {"answer": coach.CHAT_FALLBACK, "used": [], "actions": []}, False)
    answer = CHAT_UNAVAILABLE.get(out["fallback_reason"] or "", out["reply"]["answer"])
    chat_id = store.add_chat("coach", answer, out["source"], out["log_id"])
    if out["source"] == "coach":
        for raw in (out["reply"].get("actions") or [])[:coach.MAX_ACTIONS]:
            if (p := _proposal(store, raw, day)) is not None:
                store.add_action(chat_id, *p)
    return {"messages": _chat_costs(_with_actions(store.chat())), "source": out["source"],
            "fallback_reason": out["fallback_reason"]}


# Actions the coach can take from chat. The coach proposes; the engine checks each proposal
# against the athlete's data and turns it into a card; nothing runs until the athlete confirms it.

def _action_context(store: Store, day: date) -> dict:
    """What the coach needs to propose actions: the athlete's own scheduled runs (with the ids it
    may move or delete), the suggested heart-rate thresholds when ready, and whether a sync can
    run from here."""
    runs = [{"run_id": r.id, "day": r.day.isoformat(), "kind": r.kind, "distance_mi": round(r.miles, 1),
             "gain_ft": round(r.gain_ft), "minutes": None if r.minutes is None else round(r.minutes)}
            for r in store.planned_runs(day, day + timedelta(days=28))]
    inf = _suggested_thresholds(store)
    return {"scheduled_runs": runs, "suggested_thresholds": inf,
            "can_sync": bool(cli.connected_sources(get_accounts(), _account.get()))}


def _suggested_thresholds(store: Store) -> dict | None:
    """talus infer's AeT, AnT and max when all three are ready and rise in order from resting."""
    th = store.thresholds()
    try:
        inf = _inferred_thresholds(store)
    except Exception:
        return None
    if th is None or not inf or inf.get("status") != "ready" or not all(k in inf for k in ("aet", "ant", "max")):
        return None
    v = {k: round(inf[k]["value"]) for k in ("aet", "ant", "max")}
    if not th.resting < v["aet"] < v["ant"] <= v["max"]:
        return None
    return v | {"current": {"aet": round(th.aet), "ant": round(th.ant), "max": round(th.max)}}


def _day_text(d: date) -> str:
    return f"{d:%a}, {d:%b} {d.day}"


def _run_text(kind: str, miles: float, gain: float, minutes: float | None) -> str:
    size = [f"{miles:.1f} mi"] if miles else []
    if gain:
        size.append(f"{gain:,.0f} ft up")
    if minutes:
        size.append(f"{minutes:.0f} min")
    return f"{schedule.KINDS.get(kind, 'Run')}" + (f" ({', '.join(size)})" if size else "")


def _proposal(store: Store, raw: dict, day: date) -> tuple[str, dict, str] | None:
    """A proposed action as (kind, payload, summary), or None when it doesn't check out."""
    kind = raw.get("kind")

    def when(v) -> date | None:
        try:
            d = date.fromisoformat(v or "")
        except ValueError:
            return None
        return d if day <= d <= day + timedelta(days=schedule.MAX_AHEAD_DAYS) else None

    def own(run_id) -> schedule.PlannedRun | None:
        return next((r for r in store.planned_runs(day) if r.id == run_id), None)

    if kind == "schedule_run":
        d, rk = when(raw.get("day")), raw.get("run_kind")
        miles, gain, minutes = raw.get("miles") or 0, raw.get("gain_ft") or 0, raw.get("minutes")
        if d is None or rk not in schedule.KINDS or not (0 <= miles <= 100 and 0 <= gain <= 30000):
            return None
        if not miles and not minutes or minutes is not None and not 0 < minutes <= 2880:
            return None
        payload = {"day": d.isoformat(), "kind": rk, "miles": round(miles, 1), "gain_ft": round(gain),
                   "minutes": None if minutes is None else round(minutes)}
        return kind, payload, f"Add {_run_text(rk, miles, gain, minutes)} on {_day_text(d)}"
    if kind in ("move_run", "delete_run"):
        r = own(raw.get("run_id"))
        if r is None:
            return None
        what = f"your {_run_text(r.kind, r.miles, r.gain_ft, r.minutes)} on {_day_text(r.day)}"
        if kind == "delete_run":
            return kind, {"run_id": r.id}, f"Remove {what}"
        d = when(raw.get("day"))
        if d is None or d == r.day:
            return None
        return kind, {"run_id": r.id, "day": d.isoformat()}, f"Move {what} to {_day_text(d)}"
    if kind == "set_note":
        note = (raw.get("note") or "").strip()
        if not note or len(note) > coach.NOTE_MAX:
            return None
        return kind, {"note": note}, f"Update your training goals to: \u201c{note}\u201d"
    if kind == "sync":
        names = [cli.LABELS[n] for n, _ in cli.connected_sources(get_accounts(), _account.get())]
        if not names:
            return None
        return kind, {}, "Sync new activities from " + (", ".join(names[:-1]) + " and " if len(names) > 1 else "") + names[-1]
    if kind == "apply_thresholds":
        v = _suggested_thresholds(store)
        if v is None:
            return None
        c = v["current"]
        return kind, {k: v[k] for k in ("aet", "ant", "max")}, (
            f"Set heart-rate thresholds from your runs: AeT {c['aet']} to {v['aet']}, AnT {c['ant']} to {v['ant']}, "
            f"max {c['max']} to {v['max']} bpm (rescores every activity)")
    return None


def _with_actions(messages: list[dict]) -> list[dict]:
    """Each coach message with the action cards it proposed."""
    by_chat: dict[int, list[dict]] = {}
    for a in get_store().actions_for([m["id"] for m in messages if m["role"] == "coach"]):
        by_chat.setdefault(a["chat_id"], []).append(
            {k: a[k] for k in ("id", "kind", "summary", "status", "result")})
    for m in messages:
        m["actions"] = by_chat.get(m["id"], [])
    return messages


@app.post("/api/coach/actions/{action_id}/confirm")
def confirm_action(action_id: int, background: BackgroundTasks) -> dict:
    store = get_store()
    a = store.action(action_id)
    if a is None:
        raise HTTPException(404, "No such action.")
    if not store.claim_action(action_id):
        return {"id": action_id, "status": a["status"], "result": a["result"]}
    p = a["payload"]
    try:
        # A card confirmed days later can't put a run on a day that has passed.
        if a["kind"] in ("schedule_run", "move_run") and date.fromisoformat(p["day"]) < date.today():
            raise ValueError(f"{_day_text(date.fromisoformat(p['day']))} has passed. Ask the coach again.")
        if a["kind"] == "schedule_run":
            store.save_planned_run(schedule.PlannedRun(date.fromisoformat(p["day"]), p["kind"], p["miles"],
                                                       p["gain_ft"], p["gain_ft"], p["minutes"]))
            result = "Added to your calendar."
        elif a["kind"] in ("move_run", "delete_run"):
            r = next((r for r in store.planned_runs() if r.id == p["run_id"]), None)
            if r is None:
                raise ValueError("That run is no longer on your calendar.")
            if a["kind"] == "delete_run":
                store.delete_planned_run(r.id)
                result = "Removed from your calendar."
            else:
                r.day = date.fromisoformat(p["day"])
                store.save_planned_run(r)
                result = f"Moved to {_day_text(r.day)}."
        elif a["kind"] == "set_note":
            store.save_settings({"coach_note": p["note"].replace("**", "")})
            result = "Training goals updated."
        elif a["kind"] == "sync":
            out = sync_now(SyncIn(), background)
            if out.get("error") and not out.get("saved"):
                raise ValueError("The sync failed. Try Sync now under Settings.")
            result = f"Synced: {out.get('saved', 0)} new {'activity' if out.get('saved') == 1 else 'activities'}."
        elif a["kind"] == "apply_thresholds":
            th = store.thresholds()
            put_thresholds(ThresholdsIn(resting=th.resting, aet=p["aet"], ant=p["ant"], max=p["max"], sex=th.sex,
                                        threshold_pace_s_per_mi=th.threshold_pace_s_per_mi))
            result = "Thresholds updated and every activity rescored."
        else:
            raise ValueError("Unknown action.")
    except Exception as e:  # a card never stays stuck on "running"
        msg = e.detail if isinstance(e, HTTPException) else str(e) if isinstance(e, ValueError) else "That didn't work. Try again."
        store.finish_action(action_id, "failed", msg)
        return {"id": action_id, "status": "failed", "result": msg}
    store.finish_action(action_id, "done", result)
    return {"id": action_id, "status": "done", "result": result}


@app.post("/api/coach/actions/{action_id}/dismiss")
def dismiss_action(action_id: int) -> dict:
    store = get_store()
    if store.action(action_id) is None:
        raise HTTPException(404, "No such action.")
    if store.claim_action(action_id, "dismissed"):
        store.finish_action(action_id, "dismissed", "Dismissed.")
    a = store.action(action_id)
    return {"id": action_id, "status": a["status"], "result": a["result"]}


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
        # For older iPhone builds, which read the watch switch from here.
        "sync_method": "watch" if cli.watch_on(saved) else "intervals",
        "coach": {
            "enabled": cfg["coach_enabled"],
            "provider": saved.get("ai_provider", "anthropic"),
            "chatgpt": chatgpt.status(get_accounts(), current_account().id),
            "chatgpt_model": saved.get("chatgpt_model", ""),
            "key_set": (chatgpt.enabled() and chatgpt.status(get_accounts(), current_account().id)["sharing"]
                        if saved.get("ai_provider") == "chatgpt" else bool(os.environ.get("ANTHROPIC_API_KEY"))),
            "brief_model": cfg["brief_model"],
            "analysis_model": cfg["analysis_model"],
            "plan_model": cfg["plan_model"],
            "style": cfg["coach_style"],
            "ai_plans": saved.get("ai_plans", True),
            "styles": [{"key": k, "label": v["label"], "description": v["description"]}
                       for k, v in coach.STYLES.items()],
            "models": [{"id": k, "label": v["label"], "input": v["input"], "output": v["output"]}
                       for k, v in app_settings.MODELS.items()],
            # Per model, so the screen can show what a switch would cost. Brief: about two a day,
            # after the sync and after the check-in. Analysis: one per activity, at the last 30 days' rate.
            "estimate": {mode: {m: _usage_estimate(store, mode, m, calls) for m in app_settings.MODELS}
                         for mode, calls in (("brief", 60), ("analysis", analyses), ("build", 4), ("check", 4))},
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


@app.post("/api/settings/chatgpt/connect")
def connect_chatgpt(request: Request) -> dict:
    if request.url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise HTTPException(422, "Open Talus locally to connect ChatGPT. Hosted sign-in needs OpenAI approval.")
    try:
        return {"url": chatgpt.start(get_accounts(), current_account().id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/settings/chatgpt")
def get_chatgpt() -> dict:
    return chatgpt.status(get_accounts(), current_account().id)


@app.get("/api/settings/chatgpt/models")
def chatgpt_models() -> dict:
    try:
        return {"models": chatgpt.Client(get_accounts(), current_account().id).models()}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/api/settings/chatgpt")
def disconnect_chatgpt() -> dict:
    chatgpt.disconnect(get_accounts(), current_account().id)
    return {"ok": True}


class CoachIn(BaseModel):
    enabled: bool
    provider: Literal["anthropic", "chatgpt"] | None = None
    chatgpt_model: str | None = Field(default=None, max_length=200)
    # Models are set for everyone on the admin page; older apps still send these, and they're ignored.
    brief_model: str | None = None
    analysis_model: str | None = None
    monthly_cap_usd: float | None = Field(default=None, ge=0, le=1000)
    style: str | None = None  # left out keeps the saved style
    ai_plans: bool | None = None  # the AI coach builds plan drafts; left out keeps the setting


@app.put("/api/settings/coach")
def put_coach(body: CoachIn) -> dict:
    if body.style is not None and body.style not in coach.STYLES:
        raise HTTPException(422, f"Unknown style {body.style}")
    values = {"coach_enabled": body.enabled}
    if body.provider is not None:
        values["ai_provider"] = body.provider
    if body.chatgpt_model:
        try:
            models = chatgpt.Client(get_accounts(), current_account().id).models()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if body.chatgpt_model not in {m["id"] for m in models}:
            raise HTTPException(422, "Choose a model available to your ChatGPT account.")
        values["chatgpt_model"] = body.chatgpt_model
    if body.style is not None:
        values["coach_style"] = body.style
    if body.ai_plans is not None:
        values["ai_plans"] = body.ai_plans
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


class WatchIn(BaseModel):
    enabled: bool


@app.put("/api/sources/watch")
def put_watch(body: WatchIn) -> dict:
    """Turn syncing straight from a Garmin watch on or off. It runs alongside every other
    source; runs two sources both have are kept once."""
    get_store().save_settings({"watch_enabled": body.enabled})
    return {"ok": True}


class SyncMethodIn(BaseModel):
    method: Literal["intervals", "watch"]


@app.put("/api/settings/sync-method")
def put_sync_method(body: SyncMethodIn) -> dict:
    """Older iPhone builds' intervals.icu-or-watch choice: now just the watch switch."""
    return put_watch(WatchIn(enabled=body.method == "watch"))


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
    coros = accounts.credential(account.id, "coros") if account else None
    strava = accounts.credential(account.id, "strava_web") if account else None
    return {
        "intervals": {"connected": _intervals_credential() is not None,
                      "last_sync": saved.get("last_sync_intervals")
                      or (saved.get("last_sync") if (saved.get("last_sync") or {}).get("source") == "intervals" else None)},
        "garmin": {"available": _is_owner(), "connected": garmin is not None,
                   "updated_at": garmin and garmin["updated_at"],
                   "mfa_pending": bool(account) and _garmin().mfa_pending(account.id),
                   "last_sync": saved.get("last_sync_garmin")},
        "coros": {"available": _is_owner(), "connected": coros is not None,
                  "updated_at": coros and coros["updated_at"], "region": coros and coros.get("region"),
                  "last_sync": saved.get("last_sync_coros")},
        # The owner's personal fallback, never offered to anyone else (sources/strava_web).
        "strava_web": {"available": _is_owner(), "connected": strava is not None,
                       "enabled": bool(account) and accounts.strava_web_enabled(account.id),
                       "updated_at": strava and strava["updated_at"],
                       "env_override": _is_owner() and bool(os.environ.get("STRAVA_SESSION_COOKIE")),
                       "last_sync": saved.get("last_sync_strava_web")},
        "upload": {"last_import": saved.get("last_import"), "max_mb": MAX_UPLOAD_MB},
        "healthkit": {"last_sync": saved.get("last_sync_healthkit")},
        "watch": {"enabled": cli.watch_on(saved), "last_sync": saved.get("last_sync_watch"),
                  "last_attempt": saved.get("watch_sync")},
    }


def _garmin():
    from .sources import garmin
    return garmin


OWNER_SOURCES = {"garmin": "Garmin Connect", "coros": "COROS"}


def _owner_source(source: str):
    """The saved sign-in for an unofficial source, and how to save refreshed tokens. Those
    sources are tied to one person's login, so they never reach another account."""
    account = current_account()
    if not account.is_owner:
        raise HTTPException(403, f"{OWNER_SOURCES[source]} is for the owner account only.")
    cred = get_accounts().credential(account.id, source)
    if cred is None:
        raise HTTPException(409, f"Connect {OWNER_SOURCES[source]} under Data sources first.")

    def save(values: dict) -> None:
        kept = {k: v for k, v in cred.items() if k not in ("kind", "updated_at")}
        get_accounts().set_credential(account.id, source, "tokens", kept | values)
    return cred, save


class SyncIn(BaseModel):
    days: int = Field(default=30, ge=1, le=730)
    # Blank syncs every connected source. Naming one is for older iPhone builds and scripts.
    source: str | None = Field(default=None, pattern="^(intervals|garmin|coros|strava_web)$")


def _syncable(source: str | None) -> None:
    """Why a sync can't run, as the HTTP error to send: nothing connected, or a source that
    isn't this account's to pull."""
    accounts, account = get_accounts(), current_account()
    if source == "intervals" and _intervals_credential() is None:
        raise HTTPException(409, "Connect intervals.icu under Data sources first.")
    if source in OWNER_SOURCES:
        _owner_source(source)
    if source == cli.STRAVA_WEB:
        _strava_owner()
    if not cli.connected_sources(accounts, account, source):
        raise HTTPException(409, "Save a Strava session first." if source == cli.STRAVA_WEB
                            else "Connect a data source under Data sources first.")


@app.post("/api/sync")
def sync_now(body: SyncIn, background: BackgroundTasks) -> dict:
    """Pull every source this account has connected, one after another (cli.sync_all). The
    watch isn't one: only the phone or browser next to it can reach it."""
    _syncable(body.source)
    store, lines = get_store(), []
    since = date.today() - timedelta(days=body.days)
    result = cli.sync_all(get_accounts(), current_account(), store, since, log=lines.append, only=body.source)
    # The coach writes after the response, so the sync button doesn't wait on it.
    added = result.pop("added_ids")
    background.add_task(warm_coach, added)
    background.add_task(start_inferring, store.dir)
    return result | {"added": added_activities(store, added), "log": lines}


def added_activities(store: Store, ids: list[int]) -> list[dict]:
    """What an import brought in, so the apps can say which runs are new."""
    out = []
    for i in ids:
        row = store.activity(i)
        if row is not None:
            out.append({"id": row["id"], "name": row["name"], "sport": row["sport"], "start_time": row["start_time"],
                        "distance_m": row["platform_distance_m"], "gain_m": row["platform_gain_m"]})
    return sorted(out, key=lambda a: a["start_time"] or "", reverse=True)


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
    coros = bool(account) and accounts.credential(account.id, "coros") is not None
    intervals = _intervals_credential() is not None
    imp = _import_status(store)
    state = {
        "connect": intervals or garmin or coros or activities > 0,
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
        "sources": {"intervals": intervals, "garmin": garmin, "garmin_available": _is_owner(),
                    "coros": coros, "coros_available": _is_owner()},
        "resting_hint": _resting_hint(store),
        "coach": {"enabled": _coach_settings(store)["coach_enabled"], "style": _coach_settings(store)["coach_style"],
                  "key_set": _provider_client(store) is not None,
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
    source: str = Field(default="intervals", pattern="^(intervals|garmin|coros|strava_web)$")
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
        cred, save = _owner_source(body.source)
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


class CorosIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)
    region: Literal["en", "eu", "cn"] = "en"


@app.post("/api/sources/coros")
def connect_coros(body: CorosIn) -> dict:
    """Sign in to COROS once. The password is used for this sign-in and dropped; only the access
    token is kept, encrypted. Signing in to Training Hub on the web ends it, and this asks again."""
    account = current_account()
    if not account.is_owner:
        raise HTTPException(403, "COROS is for the owner account only.")
    from .sources import coros
    try:
        cred = coros.login(body.email.strip(), body.password, body.region)
    except coros.CorosError as e:
        raise HTTPException(400, str(e))
    get_accounts().set_credential(account.id, "coros", "tokens", cred)
    return {"status": "connected"}


@app.delete("/api/sources/coros")
def disconnect_coros() -> dict:
    """Forget the COROS token. Activities already synced stay."""
    get_accounts().clear_credential(current_account().id, "coros")
    return {"ok": True}


class StravaWebIn(BaseModel):
    cookie: str = Field(min_length=3, max_length=20000)  # a whole Cookie header runs a few KB


def _strava_owner() -> Account:
    account = current_account()
    if not account.is_owner:
        raise HTTPException(403, "Strava is for the owner account only.")
    return account


@app.post("/api/sources/strava-web")
def connect_strava_web(body: StravaWebIn) -> dict:
    """Owner only: check a Cookie header copied from a browser signed in to Strava, then save it
    encrypted and turn on the Strava pull in each sync. Nothing is saved when
    Strava doesn't accept it. The cookie is never sent back."""
    account = _strava_owner()
    from .sources.strava_web import client, puller
    try:
        found = puller.connect(get_accounts(), account.id, get_store().dir, body.cookie)
    except client.StravaWebError as e:
        reason = str(e).replace(" " + client.SETUP, "")  # the CLI's hint; the card says what to do
        raise HTTPException(400, f"That cookie didn't work: {reason} Nothing was saved.")
    return {"status": "connected"} | found


@app.delete("/api/sources/strava-web")
def disconnect_strava_web() -> dict:
    """Turn the Strava pull off and forget its session. Activities already synced stay."""
    account = _strava_owner()
    from .sources.strava_web import puller
    puller.disconnect(get_accounts(), account.id, get_store().dir)
    return {"ok": True}


class HealthKitWorkout(BaseModel):
    uuid: str = Field(min_length=1, max_length=64, pattern="^[A-Za-z0-9-]+$")  # it names the raw file
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
    added = result.pop("added_ids")
    background.add_task(warm_coach, added)
    background.add_task(start_inferring, store.dir)
    return result | {"added": added_activities(store, added), "log": lines}


def added_activities(store: Store, ids: list[int]) -> list[dict]:
    """What an import brought in, so the apps can say which runs are new."""
    out = []
    for i in ids:
        row = store.activity(i)
        if row is not None:
            out.append({"id": row["id"], "name": row["name"], "sport": row["sport"], "start_time": row["start_time"],
                        "distance_m": row["platform_distance_m"], "gain_m": row["platform_gain_m"]})
    return sorted(out, key=lambda a: a["start_time"] or "", reverse=True)


WATCH_KEYS_KEPT = 2000


def remember_watch_file(store: Store, key: str) -> None:
    saved = store.settings()
    keys = [k for k in saved.get("watch_files", []) if k != key] + [key]
    store.save_settings({"watch_files": keys[-WATCH_KEYS_KEPT:],
                         "last_sync_watch": datetime.now().isoformat(timespec="seconds")})


@app.get("/api/import/watch")
def watch_files() -> dict:
    """Files already pulled off the watch, so the apps download only what is new."""
    store = get_store()
    saved = store.settings()
    return {"files": saved.get("watch_files", []), "last_sync": saved.get("last_sync_watch"),
            "last_attempt": saved.get("watch_sync"), "taken": taken_spans(store)}


WATCH_TAKEN_DAYS = 60
UNSCORED_SPAN_S = 1800


def taken_spans(store: Store) -> list[list[str]]:
    """When each recent activity ran, from a synced source, as [start, end] in UTC. Files (the
    watch's own included) are left out: the watch's files are skipped by key, and a span around
    one would hide the next activity off the watch, like a cooldown right after a race. The apps skip a
    watch activity that falls inside one, so a run intervals.icu or Apple Health already brought in
    isn't downloaded off the watch only to be dropped as a duplicate. Starts are stored as local
    wall-clock time and read in the engine's zone, like every file without one."""
    since = (date.today() - timedelta(days=WATCH_TAKEN_DAYS)).isoformat()
    rows = store.db.execute(
        """SELECT a.start_time, s.summary FROM activity a LEFT JOIN activity_score s ON s.activity_id = a.id
           WHERE a.start_time >= ? AND a.source != 'file'""", (since,)).fetchall()
    out = []
    for r in rows:
        start = datetime.fromisoformat(r["start_time"])
        start = (start if start.tzinfo else start.astimezone()).astimezone(timezone.utc)
        elapsed = (json.loads(r["summary"]).get("elapsed_time_s") if r["summary"] else None) or UNSCORED_SPAN_S
        out.append([start.isoformat(timespec="seconds"), (start + timedelta(seconds=elapsed)).isoformat(timespec="seconds")])
    return sorted(out)


class WatchSyncIn(BaseModel):
    new: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=500)


@app.post("/api/import/watch/done")
def watch_sync_done(body: WatchSyncIn) -> dict:
    """The web or iOS app finished a watch sync. A clean one counts as a sync even when the watch
    had nothing new, so "last sync" and the status page stay current; a failed one is kept as the
    source's error until the next clean sync."""
    now = datetime.now().isoformat(timespec="seconds")
    update: dict = {"watch_sync": {"at": now, "error": body.error, "saved": body.new}}
    if body.error is None:
        update["last_sync_watch"] = now
    get_store().save_settings(update)
    return {"ok": True}


# The analysis is written right after a sync only for recent activities: a first sync of a
# year of history shouldn't pay for a read of every old run. Older ones get theirs when opened.
WARM_ANALYSIS_DAYS = 14


# Activity names. The coach names an activity from its peak or place and the kind of day; a name
# typed in Talus always wins, and a name the device or platform didn't make up is kept.

def _ai_name(store: Store, row, refresh: bool = False) -> dict:
    th = store.thresholds()
    detail = _detail_core(store, row, th, zones.model(th, store.settings().get("hr_zones")))
    shape, start, high = naming.features(detail)
    place = naming.places(store, *(start or (None, None)), high)
    packet = naming.packet(row, detail, place, shape)
    out = _coach(store, "name", str(row["id"]), packet, naming.rules_name(packet), refresh)
    name = naming.clean(str(out["reply"].get("name") or "")) or naming.rules_name(packet)["name"]
    if name and (out["source"] == "coach" or place["peak"] or place["area"]):
        store.set_activity_name(row["id"], ai_name=name)
    elif start is None and (store.activity_names().get(row["id"]) or {}).get("ai_name") is None:
        store.set_activity_name(row["id"], ai_name="")  # no route to name it by: the backfill stops asking
    return {"name": name, "source": out["source"], "fallback_reason": out["fallback_reason"], "cost": out["cost"]}


class NameIn(BaseModel):
    name: str | None = Field(default=None, max_length=naming.NAME_MAX)


@app.put("/api/activities/{activity_id}/name")
def rename_activity(activity_id: int, body: NameIn) -> dict:
    """Name an activity yourself; an empty name goes back to the coach's or the device's."""
    store = get_store()
    row = store.activity(activity_id)
    if row is None:
        raise HTTPException(404, "No such activity.")
    store.set_activity_name(activity_id, custom_name=(body.name or "").strip() or None)
    return _name(row, store.activity_names(), _names_on(store))


@app.post("/api/activities/{activity_id}/name/suggest")
def suggest_activity_name(activity_id: int) -> dict:
    """A fresh name from the coach, to use or edit. It becomes the coach's name for the activity;
    a name you typed stays until you save the suggestion."""
    store = get_store()
    row = store.activity(activity_id)
    if row is None or store.thresholds() is None:
        raise HTTPException(404, "No such activity.")
    return _ai_name(store, row, refresh=True)


NAME_COST_USD = 0.0002  # one Haiku call of about 700 tokens in, 30 out, rounded up
_naming: dict[str, dict] = {}
_naming_guard = threading.Lock()


def _unnamed(store: Store) -> list:
    names = store.activity_names()
    return [r for r in store.scored_activities() if naming.is_generic(r["name"])
            and (names.get(r["id"]) or {}).get("ai_name") is None]  # "" is tried and left with its name


@app.get("/api/names/backfill")
def names_backfill_status() -> dict:
    store = get_store()
    pending = len(_unnamed(store))
    with _naming_guard:
        job = dict(_naming.get(str(store.dir)) or {})
    return {"enabled": _names_on(store), "pending": pending, "estimate_usd": round(pending * NAME_COST_USD, 2),
            "running": bool(job.get("running")), "named": job.get("named", 0), "total": job.get("total", 0)}


@app.post("/api/names/backfill")
def names_backfill() -> dict:
    """Name every past activity that has a generic name, in the background, oldest first."""
    store = get_store()
    if not _names_on(store):
        raise HTTPException(409, "AI names are off. Turn them on in Settings first.")
    key = str(store.dir)
    rows = sorted(_unnamed(store), key=lambda r: r["start_time"])
    with _naming_guard:
        if (_naming.get(key) or {}).get("running"):
            return names_backfill_status()
        _naming[key] = {"running": True, "named": 0, "total": len(rows)}

    def run() -> None:
        own = Store(store.dir)  # a SQLite connection belongs to the thread that opened it
        try:
            for r in rows:
                try:
                    _ai_name(own, r)
                except Exception:  # an unreadable file keeps its name, and isn't asked about again
                    own.set_activity_name(r["id"], ai_name="")
                with _naming_guard:
                    _naming[key]["named"] += 1
        finally:
            with _naming_guard:
                _naming[key]["running"] = False

    ctx = contextvars.copy_context()  # the account, for the coach's settings and spending cap
    threading.Thread(target=ctx.run, args=(run,), name=f"names {key}", daemon=True).start()
    return names_backfill_status()


class NamesIn(BaseModel):
    enabled: bool


@app.put("/api/settings/names")
def put_names(body: NamesIn) -> dict:
    get_store().save_settings({"ai_names": body.enabled})
    return {"ok": True}


def name_new(store: Store, activity_ids: list[int]) -> list[str]:
    """Name each new activity whose device name is generic, as part of the sync that brought it
    in. Names typed in Talus or on the device are kept, and an activity named once isn't asked
    again. Runs whether or not the brief can: with no key or the coach off, the rules still name
    it from the peak or place."""
    if not _names_on(store):
        return []
    names, lines = store.activity_names(), []
    for aid in dict.fromkeys(activity_ids):
        row, n = store.activity(aid), names.get(aid) or {}
        if row is None or not row["start_time"] or not naming.is_generic(row["name"]) \
                or n.get("custom_name") or n.get("ai_name"):
            continue
        try:
            lines.append(f"Named activity {aid}: {_ai_name(store, row)['name']}")
        except Exception as e:  # noqa: BLE001 - one unreadable activity mustn't stop the rest of the sync's work
            lines.append(f"Couldn't name activity {aid}: {type(e).__name__}")
    return lines


def warm_coach(activity_ids: list[int]) -> list[str]:
    """Name the new activities, then write today's brief and each new recent activity's read right
    after a sync, so the pages open with them ready. Replies are cached as usual, so opening a
    page doesn't pay again."""
    store = get_store()
    lines = name_new(store, activity_ids)
    warm_details(store, force=True)
    try:
        b = coach_brief()
    except HTTPException as e:
        return lines + [f"Coach skipped: {e.detail}"]
    if b["fallback_reason"] in ("no_key", "off"):
        return lines + ["Coach skipped: " + ("no ANTHROPIC_API_KEY" if b["fallback_reason"] == "no_key" else "turned off in Settings")]
    lines.append(f"Coach brief: {b['reply']['call']} ({b['source']})")
    if store.plan("accepted") is not None:
        try:
            f = coach_fit()
            lines.append(f"Plan fit: {f['source']}")
        except HTTPException:
            pass
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
    if target.is_owner:
        raise HTTPException(409, "That's the owner's account.")
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
            "coach_models": _admin_models(accounts),
            "system": _system(accounts), "log": log}


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


def _system(accounts: Accounts) -> dict:
    out = admin.system(accounts)
    out["coach"] |= coach_models(accounts)
    return out


def _admin_models(accounts: Accounts) -> dict:
    store = get_store()
    month_ago = (date.today() - timedelta(days=30)).isoformat()
    analyses = sum(1 for r in store.activities() if (r["start_time"] or "")[:10] >= month_ago)
    return coach_models(accounts) | {
        "chosen": accounts.config("coach_models") is not None,
        "env": os.environ.get("TALUS_COACH_MODEL") or None,
        "models": [{"id": k, "label": v["label"], "input": v["input"], "output": v["output"]}
                   for k, v in app_settings.MODELS.items()],
        # What each model would cost on the admin's own account, the same estimate Settings used to show.
        "estimate": {mode: {m: _usage_estimate(store, mode, m, calls) for m in app_settings.MODELS}
                     for mode, calls in (("brief", 60), ("analysis", analyses))} | {"plan": _plan_estimate(store)},
    }


def _plan_estimate(store: Store, drafts: int = 4) -> dict:
    """What a plan draft costs on each model: the coach's build plus its second check."""
    out = {}
    for m in app_settings.MODELS:
        build, check = _usage_estimate(store, "build", m, drafts), _usage_estimate(store, "check", m, drafts)
        per_call = None if build["per_call"] is None else build["per_call"] + (check["per_call"] or 0)
        out[m] = {"per_call": per_call, "per_month": None if per_call is None else per_call * drafts,
                  "calls_per_month": drafts, "from_log": build["from_log"]}
    return out


class ModelsIn(BaseModel):
    brief_model: str
    analysis_model: str
    plan_model: str | None = None  # left out (older apps): keep the current choice


@app.put("/api/admin/coach-models")
def admin_coach_models(body: ModelsIn) -> dict:
    accounts, me = _admin()
    values = body.model_dump(exclude_none=True)
    if body.plan_model is None and (chosen := (accounts.config("coach_models") or {}).get("plan_model")):
        values["plan_model"] = chosen
    for m in values.values():
        if m not in app_settings.MODELS:
            raise HTTPException(422, f"Unknown model {m}")
    accounts.set_config("coach_models", values)
    accounts.log_admin(me.id, "coach_models", None, values)
    return coach_models(accounts)


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


# Status page. /api/health is open to anyone, for an "is it up" check; it says nothing about
# accounts. /api/status is the signed-in account's own, plus the server for the admin.
@app.get("/api/health")
def health() -> dict:
    return status.engine()


@app.get("/api/status")
def status_page() -> dict:
    accounts, account = get_accounts(), current_account()
    store = get_store()
    saved = store.settings()
    with _imports_guard:
        importing = dict(_imports[str(store.dir)]) if str(store.dir) in _imports else None
    out = {"engine": status.engine(), "sources": status.sources(accounts, account, store, saved),
           "data": status.data(store), "read_ahead": read_ahead(store), "importing": importing,
           "coach": status.coach(store, _coach_cap(store), bool(_coach_settings(store)["coach_enabled"]),
                                  key_available=(_provider_client(store) is not None
                                                 if store.settings().get("ai_provider") == "chatgpt" else None)),
           "server": status.server(accounts, _signup_open(accounts)) if account.is_admin else None}
    warm_details(store)
    return out
