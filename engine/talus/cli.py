"""talus command line: thresholds, sync, import, score, rescore, list, load, account.

Commands work on one account's data: the owner's (the first account) unless --user names another.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import ledger
from .accounts import Account, AccountError, Accounts
from .config import data_dir, load_env
from .models import ActivityScore, Thresholds
from .parse import parse
from .score import score
from .sources.base import RawFile, RemoteActivity, sniff_format, unwrap
from .store import Store
from .wellness import BASELINE_DAYS

WELLNESS_DAYS = BASELINE_DAYS + 6


def main(argv: list[str] | None = None) -> None:
    load_env()
    p = argparse.ArgumentParser(prog="talus")
    p.add_argument("--user", metavar="EMAIL", help="work on this account's data; default the owner's")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("thresholds", help="set heart-rate anchors (bpm)")
    t.add_argument("--resting", type=float, required=True)
    t.add_argument("--max", type=float, required=True)
    t.add_argument("--ant", type=float, required=True, help="anaerobic (lactate) threshold HR")
    t.add_argument("--aet", type=float, help="aerobic threshold HR; default 88%% of AnT, provisional")
    t.add_argument("--sex", choices=["male", "female"], default="male")
    t.add_argument("--threshold-pace", help="threshold pace per mile, e.g. 7:30, for runs without HR")

    inf = sub.add_parser("infer", help="estimate AeT, AnT and max HR from your runs")
    inf.add_argument("--days", type=int, default=180)
    inf.add_argument("--apply", action="store_true", help="save the AeT and AnT estimates as your thresholds")

    s = sub.add_parser("sync", help="pull new activities, plus overnight HRV, resting HR and sleep, from a platform")
    s.add_argument("--source", choices=SOURCES, default="intervals",
                   help="garmin and coros are unofficial: owner account only, at your own risk")
    s.add_argument("--days", type=int, default=180)
    s.add_argument("--since", type=date.fromisoformat)
    s.add_argument("--no-coach", action="store_true", help="skip writing the coach brief and reads after the sync")

    i = sub.add_parser("import", help="import FIT, GPX or TCX files, zips or folders of them (such as a watch's GARMIN folder), or an Apple Health export.zip")
    i.add_argument("files", nargs="+", type=Path)
    i.add_argument("--no-coach", action="store_true", help="skip writing the coach brief and reads after the import")

    sc = sub.add_parser("score", help="score a stored activity id or a file and print it")
    sc.add_argument("target")

    sub.add_parser("rescore", help="rescore every stored activity with the current model")

    ls = sub.add_parser("list", help="recent activities in mi and ft")
    ls.add_argument("-n", type=int, default=20)

    ld = sub.add_parser("load", help="cardio, distance, climb and descent ledgers: today and recent days")
    ld.add_argument("--days", type=int, default=14)

    dm = sub.add_parser("demo", help="fill an empty data dir with 10 weeks of sample training")
    dm.add_argument("--weeks", type=int, default=10)

    sv = sub.add_parser("serve", help="run the engine API for the web app")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to reach the engine from an iPhone on your Wi-Fi")

    ac = sub.add_parser("account", help="list accounts, create one, or reset a password")
    ac.add_argument("action", choices=["list", "create", "password"])
    ac.add_argument("email", nargs="?")

    sw = sub.add_parser("strava-web", help="owner only: set up, check or turn off the personal Strava website fallback")
    sw.add_argument("action", choices=["setup", "status", "off"])

    args = p.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn
        accounts = Accounts(data_dir())  # moves a single-user install into account 1 first
        for a in accounts.all():  # once, so pages read stored ledgers from the start
            Store(accounts.user_dir(a.id)).refresh_ledgers()
        uvicorn.run("talus.api:app", host=args.host, port=args.port)
        return
    accounts = Accounts(data_dir())
    if args.cmd == "account":
        cmd_account(accounts, args)
        return
    account = None
    if args.user:
        account = accounts.by_email(args.user)
        if account is None:
            sys.exit(f"No account {args.user}. See: talus account list")
    store = Store(accounts.user_dir(account.id) if account else default_user_dir(accounts))
    args.credential = credential_for(accounts, account, getattr(args, "source", "intervals"))
    args.accounts, args.account = accounts, account or accounts.owner()
    # The coach runs after a sync through the API's code, which reads the account from here.
    from . import api
    reset = api._account.set(account)
    try:
        {"thresholds": cmd_thresholds, "sync": cmd_sync, "import": cmd_import, "score": cmd_score,
         "rescore": cmd_rescore, "list": cmd_list, "load": cmd_load, "infer": cmd_infer,
         "demo": cmd_demo, "strava-web": cmd_strava_web}[args.cmd](store, args)
    finally:
        api._account.reset(reset)


def default_user_dir(accounts: Accounts):
    """The owner's folder. Before anyone has signed up that is account 1's, which is where an
    existing single-user install's data was moved and which the first sign-up claims."""
    owner = accounts.owner()
    return accounts.user_dir(owner.id if owner else 1)


def credential_for(accounts: Accounts, account: Account | None, source: str = "intervals") -> dict | None:
    """The saved credential to sync a source with. For intervals.icu the owner falls back to the
    .env key while none is saved."""
    account = account or accounts.owner()
    cred = accounts.credential(account.id, source) if account else None
    if source != "intervals":
        return cred
    if cred is None and (account is None or account.is_owner) and os.environ.get("INTERVALS_API_KEY"):
        cred = {"kind": "api_key", "api_key": os.environ["INTERVALS_API_KEY"],
                "athlete_id": os.environ.get("INTERVALS_ATHLETE_ID") or "0", "updated_at": None}
    return cred


def cmd_account(accounts: Accounts, args) -> None:
    if args.action == "list":
        for a in accounts.all():
            print(f"{a.id:>4}  {a.email}{'  (owner)' if a.is_owner else ''}  since {a.created_at[:10]}")
        if accounts.legacy_data_waiting():
            print("Existing data is waiting for the first account: sign up in the web app or run talus account create.")
        return
    if not args.email:
        sys.exit("Give the account's email.")
    password = getpass.getpass("New password: ")
    try:
        if args.action == "create":
            a = accounts.create(args.email, password)
            print(f"Created account {a.id} for {a.email}{' (owner)' if a.is_owner else ''}")
        else:
            a = accounts.by_email(args.email)
            if a is None:
                sys.exit(f"No account {args.email}")
            accounts.set_password(a.id, password)
            print(f"Password changed for {a.email}; its sessions were signed out.")
    except AccountError as e:
        sys.exit(str(e))


def _infer_rows(store: Store, days: int) -> list:
    since = (date.today() - timedelta(days=days)).isoformat()
    return [r for r in store.activities() if r["start_time"] and r["start_time"][:10] >= since]


def fill_infer_features(store: Store, days: int) -> int:
    """Read every run in the last `days` that has no stored inference features yet and store
    them. Returns how many were read. Slow on a first run over months of history; after that
    only new activities are read."""
    from .infer import FEATURES_VERSION, features

    have = store.infer_features(FEATURES_VERSION)
    n = 0
    for row in _infer_rows(store, days):
        if row["id"] in have:
            continue
        try:
            st = parse(store.raw(row))
        except Exception:  # a damaged or truncated file leaves that run out, not the whole estimate
            f = None
        else:
            st.sport = st.sport or row["sport"]
            f = features(st)
        store.save_infer_features(row["id"], FEATURES_VERSION, f)
        n += 1
    return n


def inferred(store: Store, days: int) -> tuple[dict, list, int]:
    """The estimates from the stored features of runs in the last `days`, the rows in that
    window, and how many of them have no features stored yet."""
    from .infer import FEATURES_VERSION, combine

    have = store.infer_features(FEATURES_VERSION)
    rows = _infer_rows(store, days)
    return combine([(r["id"], have[r["id"]]) for r in rows if r["id"] in have]), rows, \
        sum(1 for r in rows if r["id"] not in have)


def cmd_infer(store: Store, args) -> None:
    fill_infer_features(store, args.days)
    r, rows, _ = inferred(store, args.days)
    names = {row["id"]: f"{row['start_time'][:10]} {row['name'] or ''}".strip() for row in rows}
    print(f"{r['runs_with_hr']} runs with heart rate in the last {args.days} days.\n")
    for key, label in (("ant", "AnT"), ("aet", "AeT"), ("max", "Max HR")):
        e = r[key]
        value = "n/a" if e.value is None else f"{e.value:.0f} bpm"
        print(f"{label:7s} {value:8s} {e.confidence:11s} {e.method}")
        for i in e.activities:
            print(f"{'':28s}from #{i} {names.get(i, '')}")
    if r["zones"]:
        z = r["zones"]
        print(f"\nZones: 1 {z['z1']}, 2 {z['z2']}, 3 {z['z3']}")
    if r["drift_runs"]:
        print("\nDrift on steady flat running (under 5% means aerobic):")
        for d in sorted(r["drift_runs"], key=lambda d: d.avg_hr):
            print(f"  #{d.activity_id:<5d} {names.get(d.activity_id, ''):32.32s} avg {d.avg_hr:5.1f} bpm, "
                  f"drift {d.decoupling:6.1%} over {d.flat_minutes:.0f} flat min")
    current = store.thresholds()
    if current:
        print(f"\nCurrent: AeT {current.aet:.0f}, AnT {current.ant:.0f}, max {current.max:.0f} ({current.source})")
    if not args.apply:
        print("Run with --apply to use these, or set your own with talus thresholds.")
        return
    if current is None or r["ant"].value is None or r["aet"].value is None:
        sys.exit("Need current thresholds (for resting HR) and both estimates to apply.")
    th = Thresholds(current.resting, r["aet"].value, r["ant"].value, max(current.max, r["max"].value or 0),
                    current.sex, current.threshold_pace_s_per_mi,
                    f"inferred from runs: AnT {r['ant'].confidence}, AeT {r['aet'].confidence}")
    store.set_thresholds(th)
    print(f"Saved: AeT {th.aet:.0f}, AnT {th.ant:.0f}, max {th.max:.0f}. Run talus rescore to apply them to history.")


def cmd_thresholds(store: Store, args) -> None:
    pace = None
    if args.threshold_pace:
        m, sec = args.threshold_pace.split(":")
        pace = int(m) * 60 + int(sec)
    aet = args.aet or round(args.ant * 0.88)
    th = Thresholds(args.resting, aet, args.ant, args.max, args.sex, pace,
                    "manual" if args.aet else "manual, AeT provisional (88% of AnT)")
    store.set_thresholds(th)
    print(f"Saved: resting {th.resting:.0f}, AeT {th.aet:.0f}, AnT {th.ant:.0f}, max {th.max:.0f} ({th.source})")
    if store.activities():  # as Settings does, so scores and ledgers match the new thresholds
        print(f"Rescored {rescore(store, th, log=lambda _: None)} activities and rebuilt the ledgers")


def cmd_sync(store: Store, args) -> None:
    since = args.since or date.today() - timedelta(days=args.days)
    credential = args.credential
    if args.source in ("garmin", "coros") and not (args.account and args.account.is_owner):
        # Unofficial and tied to one person's login: never pull it into another account.
        sys.exit(f"{'COROS' if args.source == 'coros' else 'Garmin Connect'} is for the owner account only.")
    if args.source == "coros" and credential is None:
        sys.exit("Connect COROS in Settings > Data sources first.")
    if args.source == "garmin" and credential is None:
        # First run from the command line: sign in with GARMIN_EMAIL and GARMIN_PASSWORD from .env
        # (MFA prompts here), then keep only the tokens, like Settings does.
        from .sources import garmin
        if not (args.account and args.account.is_owner):
            sys.exit("Garmin Connect is for the owner account only.")
        if not (os.environ.get("GARMIN_EMAIL") and os.environ.get("GARMIN_PASSWORD")):
            sys.exit("Connect Garmin in Settings > Data sources, or set GARMIN_EMAIL and GARMIN_PASSWORD in .env.")
        tokens = garmin.start_login(args.account.id, os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
        if tokens is None:
            tokens = garmin.finish_login(args.account.id, input("Garmin MFA code: "))
        args.accounts.set_credential(args.account.id, "garmin", "tokens", {"tokens": tokens})
        credential = {"kind": "tokens", "tokens": tokens}
    save = None
    if args.account:
        def save(values: dict) -> None:
            args.accounts.set_credential(args.account.id, args.source, "tokens", values)
    try:
        result = sync(store, args.source, since, credential=credential, save_credential=save)
    finally:
        # Runs even when intervals.icu fails: that is when the Strava fallback matters.
        strava = strava_web_after(args.accounts, args.account, store, args.source, since)
    if not args.no_coach and store.thresholds() is not None:
        warm_coach(result["added_ids"] + (strava["added_ids"] if strava else []))


SOURCES = ("intervals", "garmin", "coros")
STRAVA_WEB = "strava_web"


def strava_web_after(accounts: Accounts, account: Account | None, store: Store, source_name: str, since: date,
                     log=print) -> dict | None:
    """After an intervals.icu sync, also pull from Strava's website (sources/strava_web): a
    personal fallback, only for the owner account with strava_web_enabled set. Its failures are
    logged and kept as last_sync_strava_web, never raised into the sync it follows."""
    if source_name != "intervals" or account is None or not account.is_owner \
            or not accounts.strava_web_enabled(account.id):
        return None
    from .sources.strava_web import puller
    cred = accounts.credential(account.id, STRAVA_WEB)
    try:
        return puller.sync(store, since, log=log, saved_cookie=cred and cred.get("cookie"))
    except Exception as e:
        log(f"Strava (personal fallback) not synced: {type(e).__name__}: {e}")
        return None


def open_source(source_name: str, credential: dict | None):
    if source_name == "intervals":
        from .sources.intervals import IntervalsSource
        if credential is None:
            raise RuntimeError("No intervals.icu key for this account. Add one in Settings > Data sources.")
        return IntervalsSource.from_credential(credential)
    if source_name == "garmin":
        from .sources.garmin import GarminSource
        if credential is None:
            raise RuntimeError("Garmin Connect isn't connected. Connect it in Settings > Data sources.")
        return GarminSource.from_credential(credential)
    if source_name == "coros":
        from .sources.coros import CorosSource
        if credential is None:
            raise RuntimeError("COROS isn't connected. Connect it in Settings > Data sources.")
        return CorosSource.from_credential(credential)
    raise ValueError(f"Unknown source {source_name}")


def sync(store: Store, source_name: str, since: date, log=print, credential: dict | None = None,
         save_credential=None, source=None, progress=None) -> dict:
    """Pull new activities since a day, score them and rebuild the ledgers. An activity another
    source already brought in (intervals.icu and Garmin both carry your watch's runs) is skipped
    before it is downloaded. The outcome is kept as that source's last sync, so the Settings
    screen can show it whether the sync ran here or from the web. `progress(done, total)` is
    called once the list is in and after each download, for the first-run import screen."""
    started = datetime.now().isoformat(timespec="seconds")
    result = {"at": started, "source": source_name, "since": since.isoformat(), "listed": 0, "new": 0,
              "saved": 0, "duplicates": 0, "error": None}
    added: list[int] = []
    try:
        source = source or open_source(source_name, credential)
        remote = source.list_activities(since)
        new = [a for a in remote if not store.has(a.source, a.source_id)]
        dups = [a for a in new if store.duplicate_of(a.start_time, a.distance_m) is not None]
        new = [a for a in new if a not in dups]
        result.update(listed=len(remote), new=len(new), duplicates=len(dups))
        log(f"{len(remote)} activities since {since}, {len(new)} new"
            + (f", {len(dups)} already here from another source" if dups else ""))
        th = store.thresholds()
        if progress:
            progress(0, len(new))
        for i, act in enumerate(sorted(new, key=lambda a: a.start_time)):
            if progress and i:
                progress(i, len(new))
            raw = source.fetch(act)
            if raw is None:
                log(f"  skip {act.start_time:%Y-%m-%d} {act.name}: no file or streams")
                continue
            activity_id = store.add(act, raw)
            added.append(activity_id)
            result["saved"] += 1
            _score_and_save(store, activity_id, raw, th, act.sport,
                            label=f"{act.start_time:%Y-%m-%d} {act.name}", log=log)
        if progress:
            progress(len(new), len(new))
        if hasattr(source, "list_wellness"):
            # Readiness reads overnight signals against a 60-day baseline, so always pull that far back.
            # Best effort: activities are already saved, and readiness works without overnight data.
            wellness_since = min(since, date.today() - timedelta(days=WELLNESS_DAYS))
            if getattr(source, "wellness_per_day", False):
                # One request per day: ask only from the last stored night, re-reading two days
                # in case a night was still syncing from the watch.
                have = store.wellness(wellness_since, date.today())
                if have:
                    wellness_since = max(wellness_since, max(have) - timedelta(days=2))
            try:
                days = source.list_wellness(wellness_since)
            except Exception as e:
                log(f"  HRV, resting HR and sleep not pulled: {type(e).__name__}: {e}")
            else:
                store.save_wellness(days)
                result["wellness_days"] = len(days)
                log(f"{len(days)} days of HRV, resting HR and sleep")
        if th is None:
            log("Set thresholds with `talus thresholds` to score these activities.")
        else:
            rebuild_ledgers(store)
    except Exception as e:  # recorded for the Settings screen, then raised as before
        result["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        store.save_settings({"last_sync": result, f"last_sync_{source_name}": result})
        if save_credential and hasattr(source, "tokens"):
            tokens = source.tokens()
            if tokens and credential and tokens != credential.get("tokens"):
                save_credential({"tokens": tokens})  # refreshed during the sync
    return result | {"added_ids": added}


STRAVA_COOKIE_STEPS = """\
Talus borrows the session from a browser where you are signed in to Strava.

  1. In Chrome, open https://www.strava.com/dashboard while signed in.
  2. Open DevTools (Cmd+Option+I), go to the Network tab and reload the page.
  3. Click the first request (dashboard), then Headers > Request Headers.
  4. Copy the whole value of the Cookie line (right-click it > Copy value).

It's never shown, and it's saved encrypted, like the Garmin tokens. You can also pipe it in:
pbpaste | talus strava-web setup. When Strava ends the session, syncs stop with
session_expired: run this again.
"""


def _paste_cookie() -> str:
    """The Cookie header, which runs past the 1,024 characters macOS lets a terminal line hold:
    piped in, else from the clipboard where pbpaste exists, else typed without echo."""
    if not sys.stdin.isatty():
        return sys.stdin.read()
    if shutil.which("pbpaste"):
        input("Copy it, then press Enter here (Talus reads it from the clipboard): ")
        return subprocess.run(["pbpaste"], capture_output=True, text=True, check=False).stdout
    return getpass.getpass("Cookie (not shown): ")


def _ask(prompt: str) -> str:
    if not sys.stdin.isatty():
        return ""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


def cmd_strava_web(store: Store, args) -> None:
    """Guided setup, status and off switch for sources/strava_web, the owner's personal fallback.
    Settings > Data sources does the same from the web app."""
    from .sources.strava_web import client as strava, puller

    accounts, account = args.accounts, args.account
    if account is None or not account.is_owner:
        sys.exit("The Strava website fallback is for the owner account only.")
    if args.action == "off":
        puller.disconnect(accounts, account.id, store.dir)
        print("Strava website sync is off, and its saved session is deleted.")
        return
    if args.action == "status":
        cred = accounts.credential(account.id, STRAVA_WEB)
        last = store.settings().get(f"last_sync_{STRAVA_WEB}")
        print(f"After each intervals.icu sync: {'on' if accounts.strava_web_enabled(account.id) else 'off'}")
        print(f"Session: {'saved ' + cred['updated_at'][:16] if cred else 'none'}"
              + (" (STRAVA_SESSION_COOKIE in .env overrides it)" if os.environ.get("STRAVA_SESSION_COOKIE") else ""))
        if last:
            print(f"Last run {last['at']}: {last['saved']} new, {last['aliased']} already here"
                  + (f", stopped: {last['error_code']} ({last['error']})" if last.get("error") else ""))
        return

    print("Strava website sync: a personal fallback for when intervals.icu can't get your runs.\n"
          "It reads your own Strava account through the website, which Strava's terms don't allow.\n"
          "Only this owner account can use it.\n")
    print(STRAVA_COOKIE_STEPS)
    pasted = _paste_cookie()
    if not strava.clean_cookie_header(pasted):
        sys.exit("Nothing pasted. Nothing was saved.")
    try:
        found = puller.connect(accounts, account.id, store.dir, pasted)
    except strava.StravaWebError as e:
        sys.exit(f"That cookie didn't work: {e}\nNothing was saved.")
    latest = found["latest"]
    print(f"Signed in: {found['total']} activities on Strava"
          + (f", the latest {datetime.fromisoformat(latest['start_time']):%a %b %-d} \"{latest['name']}\"." if latest else "."))
    print("Saved. Talus now also syncs from Strava after each intervals.icu sync.\n"
          "The cookie may still be on your clipboard: copy something else over it.")
    if _ask("Pull the last 30 days from Strava now? [y/N] ") in ("y", "yes"):
        result = strava_web_after(accounts, account, store, "intervals", date.today() - timedelta(days=30))
        if result and result["added_ids"] and store.thresholds() is not None:
            warm_coach(result["added_ids"])


def cmd_import(store: Store, args) -> None:
    from .imports import ACTIVITY_EXT, import_paths
    files = []
    for p in args.files:  # a folder, such as a copy of a watch's GARMIN folder, is read whole
        files += sorted((str(f.relative_to(p)), f) for f in p.rglob("*") if f.name.lower().endswith(ACTIVITY_EXT)) \
            if p.is_dir() else [(p.name, p)]
    result = import_paths(store, files)
    if store.thresholds() is None:
        print("Set thresholds with `talus thresholds` to score these activities.")
    elif not args.no_coach:
        warm_coach(result["added_ids"])


def warm_coach(activity_ids: list[int], log=print) -> None:
    """Run the coach right after new data lands, so Today and Activity open with it ready."""
    from . import api
    for line in api.warm_coach(activity_ids):
        log(line)


def cmd_score(store: Store, args) -> None:
    th = _require_thresholds(store)
    if Path(args.target).is_file():
        data = unwrap(Path(args.target).read_bytes())
        result = score(parse(RawFile(data, sniff_format(data))), th)
    else:
        row = store.activity(int(args.target))
        if row is None:
            sys.exit(f"No activity {args.target}")
        streams = parse(store.raw(row))
        streams.sport = streams.sport or row["sport"]
        result = score(streams, th)
        store.save_score(row["id"], result)
        rebuild_ledgers(store)
        print(report(result))
        print(usual_day_line(store, row, result))
        return
    print(report(result))


def cmd_rescore(store: Store, args) -> None:
    n = rescore(store, _require_thresholds(store))
    print(f"Rescored {n} activities and rebuilt the ledgers")


def fix_utc_starts(store: Store, log=print) -> int:
    """Files imported before Talus filed them by local time were stored with a UTC start, so an
    evening run could land on the next day. Rewrite those starts as local wall-clock time."""
    from .sources.base import local_naive, wall_clock_start
    fixed = 0
    for row in store.activities():
        start = row["start_time"]
        if not start or datetime.fromisoformat(start).tzinfo is None:
            continue
        try:
            local = wall_clock_start(parse(store.raw(row)))
        except Exception:  # unreadable file: at least move it into the engine's zone
            local = None
        local = local or local_naive(datetime.fromisoformat(start))
        store.set_start_time(row["id"], local)
        fixed += 1
    if fixed:
        log(f"Moved {fixed} imported activities from UTC to local start times")
    return fixed


def rescore(store: Store, th: Thresholds, log=print) -> int:
    fix_utc_starts(store, log=log)
    rows = store.activities()
    for row in rows:
        _score_and_save(store, row["id"], lambda row=row: store.raw(row), th, row["sport"],
                        label=f"{row['start_time'][:10]} {row['name']}", log=log)
    rebuild_ledgers(store)
    return len(rows)


def cmd_list(store: Store, args) -> None:
    import json
    print(f"{'id':>4}  {'date':10}  {'mi':>5}  {'ft up':>6}  {'ft down':>7}  {'eff mi':>6}  "
          f"{'cardio':>6}  {'climb':>6}  {'descent':>7}  name")
    for row in store.activities(args.n):
        if not row["summary"]:
            print(f"{row['id']:>4}  {row['start_time'][:10]}  (not scored)  {row['name']}")
            continue
        m = json.loads(row["summary"])
        cardio = "-" if m["cardio_load"] is None else f"{m['cardio_load']:.0f}{'*' if m['cardio_estimated'] else ''}"
        print(f"{row['id']:>4}  {row['start_time'][:10]}  {m['distance_mi']:5.1f}  {m['gain_ft']:6,.0f}  "
              f"{m['loss_ft']:7,.0f}  {m['effort_mi']:6.1f}  {cardio:>6}  {m['climb_load']:6,.0f}  "
              f"{m['descent_load']:7,.0f}  {row['name'] or ''}")


def cmd_load(store: Store, args) -> None:
    days = store.ledgers(date.today() - timedelta(days=args.days - 1))
    if not days:
        sys.exit("No ledger yet: set thresholds, then sync or import activities")
    latest = max(d.day for d in days)
    print(f"Ledgers on {latest}")
    for d in sorted((d for d in days if d.day == latest), key=lambda d: ledger.LEDGERS.index(d.ledger)):
        flags = "; ".join(ledger.FLAG_TEXT[f] for f in d.flags)
        nd = 1 if d.ledger == "distance" else 0
        print(f"  {d.ledger:8} last 7 days {ledger.describe_vs_normal(d.vs_normal):24} "
              f"(fitness {d.fitness:,.{nd}f}, fatigue {d.fatigue:,.{nd}f}, form {d.form:+,.{nd}f})"
              + (f"  ! {flags}" if flags else ""))
    history_days = len(store.ledgers()) // len(ledger.LEDGERS)
    if history_days < ledger.MIN_HISTORY_DAYS:
        print(f"  Thin history: flags start after {ledger.MIN_HISTORY_DAYS} days of data")
    print()
    print(f"{'day':10}  {'cardio':>6}  {'mi':>5}  {'climb':>6}  {'descent':>7}  flags")
    by_day: dict = {}
    for d in days:
        by_day.setdefault(d.day, {})[d.ledger] = d
    for day in sorted(by_day):
        row = by_day[day]
        flags = ", ".join(f"{k} {f}" for k, v in row.items() for f in v.flags)
        print(f"{day}  {row['cardio'].load:6,.0f}  {row['distance'].load:5.1f}  {row['climb'].load:6,.0f}  "
              f"{row['descent'].load:7,.0f}  {flags}")
    print("\nLoads: cardio 100 = 1 hour at threshold HR; distance in miles; "
          "climb and descent 1 = one foot on a moderate grade.")


def cmd_demo(store: Store, args) -> None:
    from . import demo
    if any(row["source"] != "demo" for row in store.activities()):
        sys.exit(f"{store.dir} holds real activities. Point TALUS_DATA_DIR at an empty folder for the demo.")
    if store.thresholds() is None:
        store.set_thresholds(Thresholds(45, 150, 172, 190, source="demo athlete"))
    th = store.thresholds()
    for start, name, data in demo.sessions(date.today(), args.weeks):
        raw = RawFile(data, "gpx")
        act = RemoteActivity(source="demo", source_id=f"{start:%Y%m%d}", start_time=start, name=name,
                             sport="hiking" if "hike" in name.lower() else "running")
        _score_and_save(store, store.add(act, raw), raw, th, act.sport, label=f"{start:%Y-%m-%d} {name}")
    store.save_wellness(demo.wellness(date.today(), args.weeks))
    rebuild_ledgers(store)
    print(f"Demo data in {store.dir}. Run `talus serve` and open the web app.")


def rebuild_ledgers(store: Store) -> None:
    store.refresh_ledgers()


def usual_day_line(store: Store, row, r: ActivityScore) -> str:
    day = date.fromisoformat(row["start_time"][:10])
    before = {d.ledger: d.fitness for d in store.ledgers(day - timedelta(days=1)) if d.day == day - timedelta(days=1)}
    parts = []
    for name, key in ledger.ACTIVITY_KEY.items():
        load = getattr(r, key)
        x = None if load is None else ledger.times_usual_day(load, before.get(name, 0.0))
        parts.append(f"{name} {'n/a' if x is None else f'{x:.1f}x'}")
    return "Vs a usual day     " + ", ".join(parts)


def _require_thresholds(store: Store) -> Thresholds:
    th = store.thresholds()
    if th is None:
        sys.exit("Set thresholds first: talus thresholds --resting 45 --ant 172 --max 190")
    return th


def _score_and_save(store, activity_id, raw, th, sport, label, log=print) -> None:
    if th is None:
        log(f"  saved {label}")
        return
    try:
        streams = parse(raw() if callable(raw) else raw)
        streams.sport = streams.sport or sport
        result = score(streams, th)
    except Exception as e:  # a damaged, truncated or missing file leaves that activity unscored, not the batch
        log(f"  saved {label}, not scored: {type(e).__name__}: {e}")
        return
    store.save_score(activity_id, result)
    log(f"  {label}: {result.distance_mi:.1f} mi, {result.gain_ft:,.0f} ft up, {result.loss_ft:,.0f} ft down")


def report(r: ActivityScore) -> str:
    h, rem = divmod(int(r.moving_time_s), 3600)
    cardio = "not scored" if r.cardio_load is None else f"{r.cardio_load:.0f}{' (estimated)' if r.cardio_estimated else ''}"
    lines = [
        f"Distance and vert  {r.distance_mi:.1f} mi, {r.gain_ft:,.0f} ft up, {r.loss_ft:,.0f} ft down",
        f"Effort miles       {r.effort_mi:.1f}",
        f"Moving time        {h}:{rem // 60:02d}",
        f"Loads              cardio {cardio}, climb {r.climb_load:,.0f}, descent {r.descent_load:,.0f}",
        f"Zones (min)        Z1 {r.zone_minutes['z1']:.0f}, Z2 {r.zone_minutes['z2']:.0f}, Z3 {r.zone_minutes['z3']:.0f}",
        "Gain by grade      " + ", ".join(f"{b}%: {v:,.0f} ft" for b, v in r.gain_by_band_ft.items()),
        "Loss by grade      " + ", ".join(f"{b}%: {v:,.0f} ft" for b, v in r.loss_by_band_ft.items()),
        f"Longest climb      {r.longest_climb_ft:,.0f} ft, longest descent {r.longest_descent_ft:,.0f} ft",
        f"Hike share         {r.hike_share:.0%}",
        f"Above 8,000 ft     {r.minutes_above_8000ft:.0f} min",
        f"Segments           {len(r.segments)} "
        f"({sum(s.kind == 'climb' for s in r.segments)} climbs, {sum(s.kind == 'descent' for s in r.segments)} descents)",
    ]
    lines += [f"Note               {n}" for n in r.notes]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
