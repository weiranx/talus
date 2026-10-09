"""talus command line: thresholds, sync, import, score, rescore, list, load."""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import ledger
from .config import data_dir, load_env
from .models import ActivityScore, Thresholds
from .parse import parse
from .score import score
from .sources.base import RawFile, RemoteActivity, sniff_format, unwrap
from .store import Store


def main(argv: list[str] | None = None) -> None:
    load_env()
    p = argparse.ArgumentParser(prog="talus")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("thresholds", help="set heart-rate anchors (bpm)")
    t.add_argument("--resting", type=float, required=True)
    t.add_argument("--max", type=float, required=True)
    t.add_argument("--ant", type=float, required=True, help="anaerobic (lactate) threshold HR")
    t.add_argument("--aet", type=float, help="aerobic threshold HR; default 88%% of AnT, provisional")
    t.add_argument("--sex", choices=["male", "female"], default="male")
    t.add_argument("--threshold-pace", help="threshold pace per mile, e.g. 7:30, for runs without HR")

    s = sub.add_parser("sync", help="pull new activities from a platform")
    s.add_argument("--source", choices=["intervals", "garmin"], default="intervals")
    s.add_argument("--days", type=int, default=180)
    s.add_argument("--since", type=date.fromisoformat)

    i = sub.add_parser("import", help="import FIT, GPX or TCX files")
    i.add_argument("files", nargs="+", type=Path)

    sc = sub.add_parser("score", help="score a stored activity id or a file and print it")
    sc.add_argument("target")

    sub.add_parser("rescore", help="rescore every stored activity with the current model")

    ls = sub.add_parser("list", help="recent activities in mi and ft")
    ls.add_argument("-n", type=int, default=20)

    ld = sub.add_parser("load", help="cardio, climb and descent ledgers: today and recent days")
    ld.add_argument("--days", type=int, default=14)

    args = p.parse_args(argv)
    store = Store(data_dir())
    {"thresholds": cmd_thresholds, "sync": cmd_sync, "import": cmd_import, "score": cmd_score,
     "rescore": cmd_rescore, "list": cmd_list, "load": cmd_load}[args.cmd](store, args)


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


def cmd_sync(store: Store, args) -> None:
    if args.source == "intervals":
        from .sources.intervals import IntervalsSource
        source = IntervalsSource()
    else:
        from .sources.garmin import GarminSource
        source = GarminSource()
    since = args.since or date.today() - timedelta(days=args.days)
    remote = source.list_activities(since)
    new = [a for a in remote if not store.has(a.source, a.source_id)]
    print(f"{len(remote)} activities since {since}, {len(new)} new")
    th = store.thresholds()
    for act in sorted(new, key=lambda a: a.start_time):
        raw = source.fetch(act)
        if raw is None:
            print(f"  skip {act.start_time:%Y-%m-%d} {act.name}: no file or streams")
            continue
        activity_id = store.add(act, raw)
        _score_and_save(store, activity_id, raw, th, act.sport, label=f"{act.start_time:%Y-%m-%d} {act.name}")
    if th is None:
        print("Set thresholds with `talus thresholds` to score these activities.")
    elif new:
        rebuild_ledgers(store)


def cmd_import(store: Store, args) -> None:
    th = store.thresholds()
    for path in args.files:
        data = unwrap(path.read_bytes())
        raw = RawFile(data, sniff_format(data))
        streams = parse(raw)
        act = RemoteActivity(source="file", source_id=hashlib.sha256(data).hexdigest()[:16],
                             start_time=streams.start_time or datetime.fromtimestamp(path.stat().st_mtime),
                             name=path.stem, sport=streams.sport)
        activity_id = store.add(act, raw)
        _score_and_save(store, activity_id, raw, th, act.sport, label=path.name)
    if th is not None:
        rebuild_ledgers(store)


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
    th = _require_thresholds(store)
    rows = store.activities()
    for row in rows:
        _score_and_save(store, row["id"], store.raw(row), th, row["sport"], label=f"{row['start_time'][:10]} {row['name']}")
    rebuild_ledgers(store)
    print(f"Rescored {len(rows)} activities and rebuilt the ledgers")


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
    days = store.ledger_days(date.today() - timedelta(days=args.days - 1))
    if not days:
        sys.exit("No ledger yet: set thresholds, then sync or import activities")
    latest = max(d.day for d in days)
    print(f"Ledgers on {latest}")
    for d in (d for d in days if d.day == latest):
        flags = "; ".join(ledger.FLAG_TEXT[f] for f in d.flags)
        print(f"  {d.ledger:8} last 7 days {ledger.describe_vs_normal(d.vs_normal):24} "
              f"(fitness {d.fitness:,.0f}, fatigue {d.fatigue:,.0f}, form {d.form:+,.0f})"
              + (f"  ! {flags}" if flags else ""))
    history_days = len(store.ledger_days()) // len(ledger.LEDGERS)
    if history_days < ledger.MIN_HISTORY_DAYS:
        print(f"  Thin history: flags start after {ledger.MIN_HISTORY_DAYS} days of data")
    print()
    print(f"{'day':10}  {'cardio':>6}  {'climb':>6}  {'descent':>7}  flags")
    by_day: dict = {}
    for d in days:
        by_day.setdefault(d.day, {})[d.ledger] = d
    for day in sorted(by_day):
        row = by_day[day]
        flags = ", ".join(f"{k} {f}" for k, v in row.items() for f in v.flags)
        print(f"{day}  {row['cardio'].load:6,.0f}  {row['climb'].load:6,.0f}  {row['descent'].load:7,.0f}  {flags}")
    print("\nLoads: cardio 100 = 1 hour at threshold HR; climb and descent 1 = one foot on a moderate grade.")


def rebuild_ledgers(store: Store) -> None:
    store.save_ledgers(ledger.build(store.scored_loads()))


def usual_day_line(store: Store, row, r: ActivityScore) -> str:
    day = date.fromisoformat(row["start_time"][:10])
    before = {d.ledger: d.fitness for d in store.ledger_days(day - timedelta(days=1)) if d.day == day - timedelta(days=1)}
    parts = []
    for name, load in (("cardio", r.cardio_load), ("climb", r.climb_load), ("descent", r.descent_load)):
        x = ledger.times_usual_day(load or 0.0, before.get(name, 0.0))
        parts.append(f"{name} {'n/a' if x is None else f'{x:.1f}x'}")
    return "Vs a usual day     " + ", ".join(parts)


def _require_thresholds(store: Store) -> Thresholds:
    th = store.thresholds()
    if th is None:
        sys.exit("Set thresholds first: talus thresholds --resting 45 --ant 172 --max 190")
    return th


def _score_and_save(store, activity_id, raw, th, sport, label) -> None:
    if th is None:
        print(f"  saved {label}")
        return
    try:
        streams = parse(raw)
        streams.sport = streams.sport or sport
        result = score(streams, th)
    except ValueError as e:
        print(f"  saved {label}, not scored: {e}")
        return
    store.save_score(activity_id, result)
    print(f"  {label}: {result.distance_mi:.1f} mi, {result.gain_ft:,.0f} ft up, {result.loss_ft:,.0f} ft down")


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
