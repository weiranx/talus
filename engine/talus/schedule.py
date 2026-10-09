"""Scheduled runs: a calendar of planned runs, the athlete's own and the plan's.

The athlete adds, moves and deletes runs on any future day. The accepted plan's dated sessions
(the next 7 days) fill the same calendar; a run the athlete set on a day replaces the plan's
session for that day. Completed activities match planned runs by date and type. The engine
projects the load ledgers through the scheduled runs, so the Load page can draw where they lead
and a scheduled week that would ramp too fast is flagged before it happens.
Distances are miles, vert is feet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from . import ledger
from . import plan as planner
from .loads import effort_miles

KINDS = {
    "easy": "Easy run", "long": "Long run", "hills": "Hills", "tempo": "Tempo", "intervals": "Intervals",
    "hike": "Hike", "race": "Race", "other": "Run",
}
PLAN_KIND = {"long": "long", "hills": "hills", "easy": "easy", "objective": "race"}
MAX_AHEAD_DAYS = 366
PROJECT_DAYS = 42  # the Load page draws scheduled runs this far ahead at most


@dataclass
class PlannedRun:
    day: date
    kind: str
    miles: float
    gain_ft: float = 0.0
    loss_ft: float = 0.0
    minutes: float | None = None  # left out: estimated from the athlete's pace
    zone: int | None = None  # target heart-rate zone in the athlete's zone model
    notes: str = ""
    id: int | None = None

    def json(self) -> dict:
        return asdict(self) | {"day": self.day.isoformat()}


def _hike(a: dict) -> bool:
    return any(k in (a["sport"] or "").lower() for k in ("hike", "walk", "trek"))


def match(items: list[dict], acts: list[dict]) -> None:
    """Pair each planned run with an activity on the same day: a hike with a hike or walk first,
    any run with a run first, then any activity on foot. Each activity pairs once."""
    used: set[int] = set()
    for want_hike in (True, False):
        for it in items:
            if it.get("activity") or (it["kind"] == "hike") != want_hike or it["kind"] == "rest":
                continue
            same_day = [a for a in acts if a["day"] == it["day"] and planner.is_foot(a["sport"]) and a["id"] not in used]
            best = sorted(same_day, key=lambda a: (_hike(a) != want_hike, a["start_time"]))
            if best:
                a = best[0]
                used.add(a["id"])
                it["activity"] = {k: a[k] for k in ("id", "name", "sport", "distance_mi", "gain_ft", "loss_ft",
                                                    "moving_time_s")}


def _text(r: dict, aet: float | None, zone_text: dict[int, str]) -> str:
    vol = f"{r['miles']:.1f} mi, {round(r['gain_ft'], -1):,.0f} ft up, {round(r['loss_ft'], -1):,.0f} ft down"
    mins = f"about {round(r['minutes'] / 5) * 5:.0f} min" if r["minutes"] else ""
    effort = zone_text.get(r["zone"]) if r.get("zone") else (f"under {aet:.0f} bpm" if aet and r["kind"] in
                                                            ("easy", "long", "hills", "hike") else "")
    return ", ".join(x for x in (vol, mins, effort) if x) + "."


def calendar(runs: list[PlannedRun], plan: dict | None, acts: list[dict], start: date, days: int, today: date,
             pace_min: float, aet: float | None, zone_text: dict[int, str]) -> list[dict]:
    """Every scheduled run from `start` for `days` days, oldest first, with its status:
    done (matched an activity), missed (a past day with none), today or planned. A plan
    session on a day the athlete scheduled their own run is left out."""
    end = start + timedelta(days=days - 1)
    items = []
    for r in runs:
        if start <= r.day <= end:
            minutes = r.minutes or effort_miles(r.miles, r.gain_ft) * pace_min
            items.append(r.json() | {"source": "manual", "title": KINDS.get(r.kind, "Run"), "minutes": minutes,
                                     "estimated_minutes": r.minutes is None})
    mine = {it["day"] for it in items}
    if plan is not None:
        for s in _plan_sessions(plan, acts, today):
            d = date.fromisoformat(s["day"])
            if start <= d <= end and s["day"] not in mine and s["kind"] in PLAN_KIND:
                text = planner.session_text(s, aet or 150, plan["objective"]["name"])
                items.append({"id": None, "day": s["day"], "kind": PLAN_KIND[s["kind"]], "miles": s["miles"],
                              "gain_ft": s["gain_ft"], "loss_ft": s["loss_ft"], "minutes": s["minutes"],
                              "zone": None, "notes": "", "source": "plan", "title": text["title"],
                              "estimated_minutes": True, "how": text["how"]})
    items.sort(key=lambda it: (it["day"], it["source"] != "manual", it["id"] or 0))
    match(items, acts)
    for it in items:
        it.setdefault("activity", None)
        it.setdefault("how", None)
        it["detail"] = _text(it, aet, zone_text)
        d = date.fromisoformat(it["day"])
        it["status"] = ("done" if it["activity"] else "today" if d == today else "missed" if d < today
                        else "planned")
    return items


def _plan_sessions(plan: dict, acts: list[dict], today: date) -> list[dict]:
    return planner.upcoming_sessions(plan, acts, today)


def project(items: list[dict], history: list[ledger.LedgerDay], rates: dict, today: date,
            done_today: bool) -> dict:
    """Carry the ledgers forward through the scheduled runs that are still ahead, to the last
    one (at most PROJECT_DAYS out). Rest days in between are rest. Returns the projected days
    per ledger and the calendar weeks that would ramp too fast or overreach."""
    first = today + timedelta(days=1 if done_today else 0)
    ahead = [it for it in items if date.fromisoformat(it["day"]) >= first and it["status"] != "done"]
    if not ahead:
        return {"series": {}, "warnings": [], "until": None}
    last = min(max(date.fromisoformat(it["day"]) for it in ahead), today + timedelta(days=PROJECT_DAYS))
    by_day: dict[date, dict[str, float]] = {}
    for it in ahead:
        d = date.fromisoformat(it["day"])
        if d > last:
            continue
        loads = by_day.setdefault(d, {"cardio": 0.0, "distance": 0.0, "climb": 0.0, "descent": 0.0})
        loads["cardio"] += it["minutes"] / 60 * rates["cardio_per_hour"]
        loads["distance"] += it["miles"]
        loads["climb"] += it["gain_ft"] * rates["climb_per_ft"]
        loads["descent"] += it["loss_ft"] * rates["descent_per_ft"]
    proj = planner._Projector([d for d in history if d.day < first])
    series: dict[str, list[dict]] = {n: [] for n in proj.hist}
    warnings = []
    day = first
    while day <= last:
        week_end = min(day + timedelta(days=6 - day.weekday()), last)
        days = [(day + timedelta(days=k), by_day.get(day + timedelta(days=k), {}))
                for k in range((week_end - day).days + 1)]
        hist, bad = proj.trial(days)
        proj.commit(hist)
        for n, h in hist.items():
            for d in h[-len(days):]:
                series[n].append({"day": d.day.isoformat(), "fitness": d.fitness, "fatigue": d.fatigue,
                                  "flags": d.flags})
        if bad:
            names = sorted({b[0] for b in bad})
            over = any(b[1] == "overreach" for b in bad)
            warnings.append({"week": (day - timedelta(days=day.weekday())).isoformat(), "ledgers": names,
                             "key": "overreach" if over else "ramp",
                             "text": f"The runs scheduled this week would {'push' if over else 'ramp'} "
                                     f"{planner._join(names)} load {'into overreach' if over else 'too fast'}."})
        day = week_end + timedelta(days=1)
    return {"series": series, "warnings": warnings, "until": last.isoformat()}
