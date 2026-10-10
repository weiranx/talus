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
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie

import httpx
import numpy as np
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import cli, coach, explain, google_auth, ledger, readiness_score, wellness, zones
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
                        "hike_share", "notes")}, "hr_zone_minutes": _display_minutes(m, zm),
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
        "projection": _projection(store, day),
    }


def _projection(store: Store, day: date) -> dict:
    """Where the scheduled runs take each ledger, drawn after today on the Load charts."""
    acts = _activities(store, day - timedelta(days=planner.CAPACITY_DAYS))
    items = _calendar(store, day, schedule.PROJECT_DAYS + 1, acts)
    done_today = any(a["day"] == day.isoformat() and planner.is_foot(a["sport"]) for a in acts)
    return schedule.project(items, _plan_history(store, day), planner.rates(acts, day), day, done_today)


@app.get("/api/activities")
def activities(limit: int = 50) -> list[dict]:
    return _activities(get_store())[:limit]


def _streams(store: Store, row):
    """An activity's parsed file, or a 422 that says why it can't be read."""
    try:
        streams = parse(store.raw(row))
    except Exception as e:
        raise HTTPException(422, f"Could not read this activity's file ({type(e).__name__}: {e}).") from e
    streams.sport = streams.sport or row["sport"]
    return streams


@app.get("/api/activities/{activity_id}")
def activity(activity_id: int) -> dict:
    store = get_store()
    row = store.activity(activity_id)
    th = store.thresholds()
    if row is None or th is None:
        raise HTTPException(404, "No such activity, or thresholds not set")
    streams = _streams(store, row)
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
    # Plan, weekly review and chat are longer reads, so they run on the analysis model.
    model_id, style = cfg.get(f"{mode}_model", cfg["analysis_model"]), cfg["coach_style"]
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
    return [s | planner.session_text(s, aet, name)
            for s in planner.upcoming_sessions(accepted, acts, day, days, own=_own(store, day))]


def _zone_text(zm: dict | None) -> dict[int, str]:
    return {} if zm is None else {z["zone"]: f"zone {z['zone']}, {z['text']}" for z in zm["list"]}


def _calendar(store: Store, start: date, days: int, acts: list[dict] | None = None) -> list[dict]:
    day = date.today()
    acts = _activities(store, min(start, day) - timedelta(days=planner.CAPACITY_DAYS)) if acts is None else acts
    th = store.thresholds()
    return schedule.calendar(store.planned_runs(start, start + timedelta(days=days - 1)), store.plan("accepted"),
                             acts, start, days, day, planner.pace(acts, day)["min_per_effort_mi"],
                             th.aet if th else None, _zone_text(_zone_model(store)), own=_own(store, day))


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


@app.put("/api/plan/constraints")
def put_constraints(body: ConstraintsIn) -> dict:
    """Training days and time limits. With an objective set, a new plan is drafted from them."""
    if any(d < 0 or d > 6 for d in body.days):
        raise HTTPException(422, "Days are 0 (Monday) to 6 (Sunday).")
    if body.long_day not in body.days:
        raise HTTPException(422, "Your long day has to be one of your training days.")
    store = get_store()
    c = planner.Constraints.from_json(body.model_dump())
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
    return {"messages": get_store().chat()}


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
                               [a for a in acts if a["day"] >= (day - timedelta(days=13)).isoformat()], day)
    store.add_chat("athlete", question)
    out = _coach(store, "chat", "chat", packet, {"answer": coach.CHAT_FALLBACK, "used": []}, False)
    answer = CHAT_UNAVAILABLE.get(out["fallback_reason"] or "", out["reply"]["answer"])
    store.add_chat("coach", answer, out["source"], out["log_id"])
    return {"messages": store.chat(), "source": out["source"], "fallback_reason": out["fallback_reason"]}


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
            "updated_at": cred and cred["updated_at"],
            "garmin_fallback_set": _is_owner() and bool(os.environ.get("GARMIN_EMAIL")),
            "activities": len(acts),
            "latest_activity": max((r["start_time"][:10] for r in acts), default=None),
            "last_sync": saved.get("last_sync"),
        },
        "sources": _sources(saved),
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
    get_accounts().clear_credential(current_account().id, "intervals")
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
            raise HTTPException(409, "Add your intervals.icu API key under Data sources first.")
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
async def import_file(request: Request, background: BackgroundTasks, name: str = "upload") -> dict:
    """Upload one file as the request body: a FIT, GPX or TCX file (gzipped or not), a zip of
    them, or the Health app's export.zip. Streamed to disk, since an Apple Health export can run
    to gigabytes, and deleted once read."""
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
    background.add_task(warm_coach, result.pop("added_ids"))
    background.add_task(start_inferring, store.dir)
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


def _back(path: str, error: str | None = None) -> RedirectResponse:
    from urllib.parse import urlencode

    target = path if error is None else f"{path}?{urlencode({'google_error': error})}"
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(OAUTH_COOKIE, path="/")
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
    response = _back(flow["next"])
    _signed_in(response, accounts, account)
    return response


@app.post("/api/account/google/unlink")
def google_unlink() -> dict:
    try:
        get_accounts().unlink_identity(current_account().id, "google")
    except AccountError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}
