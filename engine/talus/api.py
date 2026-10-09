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
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import cli, coach, ledger, wellness
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


def _ledgers_through_today(store: Store) -> None:
    store.save_ledgers(ledger.build(store.scored_loads()))


def _ledger_json(d: ledger.LedgerDay, since: date | None = None) -> dict:
    """`since` is where the current run of data starts; until it holds MIN_HISTORY_DAYS the
    normal is still being built and reads as such."""
    building = since is not None and (d.day - since).days + 1 < ledger.MIN_HISTORY_DAYS
    return {"day": d.day.isoformat(), "load": d.load, "fitness": d.fitness, "fatigue": d.fatigue,
            "form": d.form, "vs_normal": d.vs_normal, "vs_normal_text": ledger.describe_vs_normal(d.vs_normal),
            "flags": [{"key": f, "text": ledger.FLAG_TEXT[f]} for f in d.flags], "building": building}


def _history(store: Store) -> dict:
    """Where the current run of data starts, and the gap before it if there is one."""
    days = sorted({d.day for d in store.ledger_days() if d.ledger == "cardio"})
    if not days:
        return {"since": None, "gap": None}
    span = ledger.current_span([ledger.LedgerDay(d, "cardio", 0, 0, 0) for d in days])
    since = span[0].day
    before = [d for d in days if d < since]
    gap = None if not before else {"from": (before[-1] + timedelta(days=1)).isoformat(),
                                   "to": (since - timedelta(days=1)).isoformat()}
    return {"since": since, "gap": gap}


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
    recent = ledger.current_span(store.ledger_days(day - timedelta(days=41)))
    since = _history(store)["since"]
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
        "ledgers": {name: _ledger_json(now[name], since) | {"spark": [
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
    # Charts start after the last gap in the data: months with nothing synced are not drawn.
    series = ledger.current_span(store.ledger_days(day - timedelta(days=days - 1)))
    hist = _history(store)
    acts = _activities(store, day - timedelta(days=55))
    weeks = []
    for w in range(7, -1, -1):
        start = day - timedelta(days=day.weekday()) - timedelta(weeks=w)
        in_week = [a for a in acts if start.isoformat() <= a["day"] < (start + timedelta(days=7)).isoformat()]
        weeks.append({"start": start.isoformat(), **_zone_share(in_week)})
    return {
        "date": day.isoformat(),
        "ledgers": {name: {"today": _ledger_json([d for d in series if d.ledger == name][-1], hist["since"])
                           if series else None,
                           "series": [_ledger_json(d, hist["since"]) for d in series if d.ledger == name]}
                    for name in ledger.LEDGERS},
        "history": {"since": hist["since"] and hist["since"].isoformat(), "gap": hist["gap"]},
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
    model_id = cfg[f"{mode}_model"]
    h = coach.packet_hash({**packet, "_model": model_id})
    row = None if refresh else store.coach_entry(mode, subject, coach.PROMPT_VERSION, h)
    if row is not None and not (row["fallback_reason"] or "").startswith("api_error"):
        return {"source": row["source"], "reply": json.loads(row["reply"]), "log_id": row["id"],
                "feedback": row["feedback"], "fallback_reason": row["fallback_reason"], "model": row["model"]}
    cap = _coach_cap(store)
    if cap["limit"] is not None and cap["spent"] >= cap["limit"]:
        return {"source": "rules", "reply": fallback, "log_id": None, "feedback": None,
                "fallback_reason": "cap", "model": None}
    result = coach.run(mode, packet, fallback, coach_client(), model_id)
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


# Settings. Keys stay in .env: the engine reports whether each one is set, never its value.

def _zones(th: Thresholds) -> list[dict]:
    """The three intensity zones the scorer uses: under AeT, AeT to AnT, over AnT."""
    return [{"zone": 1, "low": None, "high": th.aet, "text": f"under {th.aet:.0f} bpm"},
            {"zone": 2, "low": th.aet, "high": th.ant, "text": f"{th.aet:.0f} to {th.ant:.0f} bpm"},
            {"zone": 3, "low": th.ant, "high": None, "text": f"over {th.ant:.0f} bpm"}]


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
            "aet_provisional": "provisional" in th.source, "zones": _zones(th),
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
            "models": [{"id": k, "label": v["label"], "input": v["input"], "output": v["output"]}
                       for k, v in app_settings.MODELS.items()],
            # Per model, so the screen can show what a switch would cost. Brief: one a day. Analysis:
            # one per activity, at the last 30 days' rate.
            "estimate": {mode: {m: _usage_estimate(store, mode, m, calls) for m in app_settings.MODELS}
                         for mode, calls in (("brief", 30), ("analysis", analyses))},
            "cap": _coach_cap(store),
        },
        "units": {"distance": "mi", "vert": "ft"},
    }


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


@app.put("/api/settings/coach")
def put_coach(body: CoachIn) -> dict:
    for m in (body.brief_model, body.analysis_model):
        if m not in app_settings.MODELS:
            raise HTTPException(422, f"Unknown model {m}")
    values = {"coach_enabled": body.enabled, "brief_model": body.brief_model,
              "analysis_model": body.analysis_model}
    if "monthly_cap_usd" in body.model_fields_set:
        values["coach_monthly_cap_usd"] = body.monthly_cap_usd
    get_store().save_settings(values)
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
def sync_now(body: SyncIn) -> dict:
    store = get_store()
    cred = _intervals_credential()
    if cred is None:
        raise HTTPException(409, "Add your intervals.icu API key under Data source first.")
    lines: list[str] = []
    try:
        result = cli.sync(store, "intervals", date.today() - timedelta(days=body.days), log=lines.append,
                          credential=cred)
    except Exception:
        result = store.settings()["last_sync"]
    return result | {"log": lines}


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
