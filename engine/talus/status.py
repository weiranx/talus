"""What the status page shows: whether each part of Talus is working, for the signed-in account,
and for the admin the server as a whole.

Each check has a state: "ok", "warn" (working, but worth a look), "bad" (not working) or "idle"
(nothing to check yet). The page sums them up in one line.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

from . import MODEL_VERSION, admin, cli, google_auth, intervals_auth
from .accounts import Account, Accounts
from .store import Store

STARTED = time.time()

# A connected sync source that hasn't run for this long is worth a look.
STALE_SYNC_DAYS = 3


def engine() -> dict:
    """Safe for anyone: the public "is it up" check reads only this."""
    return {"ok": True, "revision": admin.revision(), "model_version": MODEL_VERSION,
            "started_at": datetime.fromtimestamp(STARTED).isoformat(timespec="seconds"),
            "uptime_s": round(time.time() - STARTED)}


def _age_days(at: str | None) -> float | None:
    if not at:
        return None
    try:
        then = datetime.fromisoformat(at)
    except ValueError:
        return None
    now = datetime.now(then.tzinfo) if then.tzinfo else datetime.now()
    return (now - then).total_seconds() / 86400


def _run(key: str, label: str, run, connected: bool | None, synced: bool) -> dict:
    """One data source: its last run and whether that went through. `synced` sources are pulled
    by Talus, so a long gap since the last run is a warning; uploads only run when someone uploads."""
    if isinstance(run, str):  # the watch keeps only a time
        run = {"at": run, "error": None}
    run = run if isinstance(run, dict) else {}
    at, error = run.get("at"), run.get("error")
    saved = run.get("saved", run.get("workouts_added"))
    if error:
        state = "bad"
    elif connected is False:
        state = "warn" if at else "idle"
    elif not at:
        state = "warn" if synced and connected else "idle"
    elif synced and (_age_days(at) or 0) > STALE_SYNC_DAYS:
        state = "warn"
    else:
        state = "ok"
    return {"key": key, "label": label, "state": state, "connected": connected, "at": at, "error": error,
            "saved": saved}


def sources(accounts: Accounts, account: Account, store: Store, saved: dict) -> list[dict]:
    """Every way data reaches this account that is set up or has been used."""
    intervals = cli.credential_for(accounts, account)
    garmin = accounts.credential(account.id, "garmin")
    coros = accounts.credential(account.id, "coros")
    watch = cli.watch_on(saved)
    last_intervals = saved.get("last_sync_intervals") or (
        saved.get("last_sync") if (saved.get("last_sync") or {}).get("source") == "intervals" else None)
    rows = []
    if intervals or last_intervals or not watch:
        row = _run("intervals", "intervals.icu", last_intervals, intervals is not None, synced=True)
        row["how"] = None if intervals is None else ("connected app" if intervals.get("kind") == "oauth" else "API key")
        rows.append(row)
    if garmin or saved.get("last_sync_garmin"):
        rows.append(_run("garmin", "Garmin Connect", saved.get("last_sync_garmin"), garmin is not None, synced=True))
    if coros or saved.get("last_sync_coros"):
        rows.append(_run("coros", "COROS", saved.get("last_sync_coros"), coros is not None, synced=True))
    strava = account.is_owner and accounts.strava_web_enabled(account.id)
    if strava or saved.get(f"last_sync_{cli.STRAVA_WEB}"):
        rows.append(_run(cli.STRAVA_WEB, "Strava", saved.get(f"last_sync_{cli.STRAVA_WEB}"), strava, synced=True))
    if watch or saved.get("last_sync_watch"):
        run = saved.get("watch_sync") or saved.get("last_sync_watch")
        rows.append(_run("watch", "Garmin watch", run, None, synced=watch))
    if saved.get("last_sync_healthkit"):
        rows.append(_run("healthkit", "Apple Health", saved.get("last_sync_healthkit"), None, synced=False))
    if saved.get("last_import"):
        rows.append(_run("upload", "File upload", saved.get("last_import"), None, synced=False))
    return rows


def data(store: Store) -> dict:
    """The newest activity and night of overnight data this account has."""
    newest = store.db.execute("SELECT MAX(start_time) FROM activity").fetchone()[0]
    count = store.db.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
    night = store.db.execute("SELECT MAX(day) FROM wellness").fetchone()[0]
    return {"activities": count, "newest_activity": newest, "newest_night": night}


def coach(store: Store, cap: dict, coach_on: bool) -> dict:
    """Whether the coach can write, its last call, and this month's spending against the cap."""
    key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    last = store.db.execute("SELECT created_at, mode, model, source, fallback_reason FROM coach_log "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    since = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    errors = store.db.execute("SELECT COUNT(*), MAX(created_at) FROM coach_log WHERE created_at >= ? "
                              "AND fallback_reason LIKE 'api_error%'", (since,)).fetchone()
    over = cap["limit"] is not None and cap["spent"] >= cap["limit"]
    last_failed = bool(last and (last["fallback_reason"] or "").startswith("api_error"))
    if not coach_on:
        state, why = "idle", "off"
    elif not key_set:
        state, why = "bad", "no_key"
    elif over:
        state, why = "warn", "cap"
    elif last_failed:
        state, why = "bad", "api_error"
    elif errors[0]:
        state, why = "warn", "recent_errors"
    else:
        state, why = "ok", None
    return {"state": state, "why": why, "enabled": coach_on, "key_set": key_set, **cap,
            "last_call": last and {"at": last["created_at"], "mode": last["mode"], "model": last["model"],
                                   "ok": last["source"] != "rules", "fallback_reason": last["fallback_reason"]},
            "errors_24h": errors[0], "last_error_at": errors[1]}


def server(accounts: Accounts, signup_open: bool) -> dict:
    """The admin's view: the server, every account's last sync and the coach's total spending."""
    month = date.today().replace(day=1)
    rows = [admin.account_row(accounts, a, month) for a in accounts.all()]
    failing = [{"email": r["email"], "source": r["last_sync"]["source"], "at": r["last_sync"]["at"]}
               for r in rows if r["last_sync"] and not r["last_sync"]["ok"]]
    disk = admin.system(accounts)
    free = disk["disk"]["free"] / disk["disk"]["total"] if disk["disk"]["total"] else 1
    return {**disk, "disk_state": "bad" if free < 0.05 else "warn" if free < 0.15 else "ok",
            "accounts": len(rows), "active_7d": sum(1 for r in rows if (_age_days(r["last_seen"]) or 99) <= 7),
            "failing_syncs": failing, "coach_spent": round(sum(r["coach"]["spent"] for r in rows), 4),
            "month": month.isoformat(), "signup_open": signup_open,
            "sign_in": {"google": google_auth.enabled()},
            "intervals_oauth": {"enabled": intervals_auth.enabled(),
                                "connected": accounts.db.execute(
                                    "SELECT COUNT(*) FROM credential WHERE source = 'intervals' AND kind = 'oauth'"
                                ).fetchone()[0]}}
