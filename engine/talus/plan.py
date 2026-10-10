"""Objectives, objective readiness, the training plan and the weekly review.

The engine owns every number here; the coach only explains them. An objective is a race or a
mountain day with a date. Objective readiness compares what the route demands with what the
last 8 weeks of training have proven, one dimension at a time. The plan builder sets weekly
targets for miles, feet up, feet down and time from today to the objective, with build,
recovery, taper and race weeks, and checks every week by projecting the load ledgers forward:
a week that would trip the ramp or overreach flag on any ledger is cut until it doesn't.
Limits are never loosened to hit a date; if the objective is out of reach the plan says so and
offers a smaller version or a later date.

Rules and percentages are the spec's starting heuristics, not validated constants. Distances are
miles, vert is feet, time is hours or minutes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta

from . import ledger
from .ledger import LedgerDay
from .loads import effort_miles

PLAN_LEDGERS = ("cardio", "climb", "descent")
CAPACITY_DAYS = 56  # biggest days and longest descent: the last 8 weeks
VOLUME_DAYS = 28  # weekly gain and altitude: the last 4 weeks
CLOSE_POINTS = 0.20  # "close" is within 20 points of the rule

# key, name, what the route demands, what training has proven, rule (capacity / demand), unit
DIMENSIONS = (
    ("big_day", "Big-day size", "Effort miles", "Largest single day, last 8 weeks", 0.70, "effort mi"),
    ("time_on_feet", "Time on feet", "Estimated time", "Longest single day, last 8 weeks", 0.60, "h"),
    ("climbing_volume", "Climbing volume", "Total gain", "Average weekly gain, last 4 weeks", 1.25, "ft"),
    ("descent_durability", "Descent durability", "Longest continuous descent",
     "Longest continuous descent, last 8 weeks", 0.75, "ft"),
    ("descent_volume", "Descent volume", "Total loss", "Descent normal times 7", 1.25, "ft"),
    ("altitude", "Altitude", "High point", "Hours above 8,000 ft, last 4 weeks", None, None),
)
STATUS_TEXT = {"ready": "Ready", "close": "Close", "gap": "Gap", "unknown": "Not given", "context": "Context"}
FITNESS_ONLY = ("This verdict covers fitness only. Weather, snow, route-finding and technical difficulty "
                "are outside the model.")

# Time estimate: the athlete's own minutes per effort mile, with a fade on long days.
DEFAULT_PACE = 15.0  # min per effort mile, until there are enough foot days to learn it
FADE_FROM_H = 3.0
FADE_PER_H = 0.02  # 2% slower for every hour past three
TIME_RANGE = (0.85, 1.10)

# Plan shape
BUILD_RATE = 1.10  # a build week adds at most 10% to the last one, per dimension
RECOVERY_EVERY = 4  # a recovery week after every three build weeks
RECOVERY = 0.70
TAPER = 0.70
RACE_WEEK = 0.50
LONG_SHARE = {"miles": 0.45, "gain_ft": 0.5, "loss_ft": 0.5}  # the long day's share of the week
LONG_TARGET = 0.72  # long-day size against the objective, just above the 70% big-day rule
VOLUME_TARGET = 1.35  # weekly gain and loss against the objective, above the 1.25 rule
DURABILITY_TARGET = 0.85
CUT_STEP = 0.90  # a week over a ledger limit is cut 10% at a time
MIN_CUT = 0.30
MAX_LATER_WEEKS = 26
FOOT = ("run", "hike", "walk", "mountain", "snowshoe", "trek")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class PlanError(ValueError):
    pass


@dataclass
class Objective:
    name: str
    day: date
    distance_mi: float
    gain_ft: float
    loss_ft: float
    longest_climb_ft: float | None = None
    longest_descent_ft: float | None = None
    high_point_ft: float | None = None
    source: str = "manual"  # or "gpx"
    id: int | None = None

    def json(self) -> dict:
        return asdict(self) | {"day": self.day.isoformat()}


@dataclass
class Constraints:
    days: list[int] = field(default_factory=lambda: [1, 2, 3, 5, 6])  # 0 is Monday
    weekday_min: int = 90
    weekend_min: int = 360
    long_day: int = 5
    notes: str = ""

    @classmethod
    def from_json(cls, d: dict | None) -> Constraints:
        c = cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})
        c.days = sorted({int(x) for x in c.days if 0 <= int(x) <= 6})
        return c

    def cap(self, day: date) -> int:
        return self.weekend_min if day.weekday() >= 5 else self.weekday_min


def is_foot(sport: str | None) -> bool:
    return sport is None or any(k in sport.lower() for k in FOOT)


def _within(acts: list[dict], day: date, days: int, foot: bool = True) -> list[dict]:
    first = (day - timedelta(days=days - 1)).isoformat()
    return [a for a in acts if first <= a["day"] <= day.isoformat() and (not foot or is_foot(a["sport"]))]


# Objective readiness -------------------------------------------------------------------------

def pace(acts: list[dict], day: date) -> dict:
    """Minutes per effort mile on foot over the last 8 weeks, from days of 2 effort miles or more."""
    days = [a for a in _within(acts, day, CAPACITY_DAYS) if a["effort_mi"] >= 2 and a["moving_time_s"] > 0]
    if len(days) < 3:
        return {"min_per_effort_mi": DEFAULT_PACE, "learned": False, "days": len(days)}
    return {"min_per_effort_mi": sum(a["moving_time_s"] for a in days) / 60 / sum(a["effort_mi"] for a in days),
            "learned": True, "days": len(days)}


def estimate_time(effort_mi: float, min_per_effort_mi: float) -> dict:
    base = effort_mi * min_per_effort_mi / 60
    mid = base * (1 + FADE_PER_H * max(0.0, base - FADE_FROM_H))
    return {"low_h": mid * TIME_RANGE[0], "mid_h": mid, "high_h": mid * TIME_RANGE[1]}


def demand(obj: Objective, p: dict) -> dict:
    effort = effort_miles(obj.distance_mi, obj.gain_ft)
    t = estimate_time(effort, p["min_per_effort_mi"])
    return {"distance_mi": obj.distance_mi, "gain_ft": obj.gain_ft, "loss_ft": obj.loss_ft, "effort_mi": effort,
            "longest_climb_ft": obj.longest_climb_ft, "longest_descent_ft": obj.longest_descent_ft,
            "high_point_ft": obj.high_point_ft, "time": t | {"pace_learned": p["learned"]},
            "dims": {"big_day": effort, "time_on_feet": t["mid_h"], "climbing_volume": obj.gain_ft,
                     "descent_durability": obj.longest_descent_ft, "descent_volume": obj.loss_ft,
                     "altitude": obj.high_point_ft}}


def capacity(acts: list[dict], descent_fitness: float, day: date) -> dict:
    """What training has proven by `day`, in the same terms as the demand."""
    last8, last4 = _within(acts, day, CAPACITY_DAYS), _within(acts, day, VOLUME_DAYS)
    return {"big_day": max((a["effort_mi"] for a in last8), default=0.0),
            "time_on_feet": max((a["moving_time_s"] for a in last8), default=0.0) / 3600,
            "climbing_volume": sum(a["gain_ft"] for a in last4) / 4,
            "descent_durability": max((a.get("longest_descent_ft") or 0.0 for a in last8), default=0.0),
            "descent_volume": descent_fitness * 7,
            "altitude": sum(a.get("minutes_above_8000ft") or 0.0 for a in last4) / 60}


def evaluate(dem: dict, cap: dict) -> dict:
    """Each dimension reads ready, close or gap; the limiter is the furthest behind its rule."""
    dims = []
    for key, name, dem_text, cap_text, rule, unit in DIMENSIONS:
        need, have = dem["dims"].get(key), cap.get(key)
        ratio = None if not need else have / need
        if rule is None:
            status = "context"
        elif ratio is None:
            status = "unknown"
        elif ratio >= rule:
            status = "ready"
        elif ratio >= rule - CLOSE_POINTS:
            status = "close"
        else:
            status = "gap"
        dims.append({"key": key, "name": name, "demand_label": dem_text, "capacity_label": cap_text,
                     "demand": need, "capacity": have, "unit": unit, "rule": rule, "ratio": ratio,
                     "status": status, "status_text": STATUS_TEXT[status]})
    behind = [d for d in dims if d["status"] in ("close", "gap")]
    limiter = min(behind, key=lambda d: d["ratio"] / d["rule"])["key"] if behind else None
    statuses = {d["status"] for d in dims}
    verdict = "gap" if "gap" in statuses else "close" if "close" in statuses else "ready"
    return {"dims": dims, "limiter": limiter, "verdict": verdict}


def readiness_now(obj: Objective, acts: list[dict], descent_fitness: float, today: date) -> dict:
    p = pace(acts, today)
    dem = demand(obj, p)
    return {"demand": dem, "capacity": capacity(acts, descent_fitness, today), "pace": p,
            "days_to_go": (obj.day - today).days, **evaluate(dem, capacity(acts, descent_fitness, today))}


# Plan builder --------------------------------------------------------------------------------

DIMS = ("miles", "gain_ft", "loss_ft")


def baseline(acts: list[dict], today: date) -> dict:
    """The last 4 weeks on foot, per week."""
    last4 = _within(acts, today - timedelta(days=1), VOLUME_DAYS)
    return {"miles": sum(a["distance_mi"] for a in last4) / 4, "gain_ft": sum(a["gain_ft"] for a in last4) / 4,
            "loss_ft": sum(a["loss_ft"] for a in last4) / 4,
            "hours": sum(a["moving_time_s"] for a in last4) / 4 / 3600}


def rates(acts: list[dict], today: date) -> dict:
    """How this athlete's miles, feet and hours turn into ledger load, from the last 8 weeks."""
    recent = _within(acts, today, CAPACITY_DAYS, foot=False)
    hr = [a for a in recent if a["cardio_load"] is not None and a["moving_time_s"] > 0]
    hours = sum(a["moving_time_s"] for a in hr) / 3600
    gain = sum(a["gain_ft"] for a in recent)
    loss = sum(a["loss_ft"] for a in recent)
    return {"cardio_per_hour": sum(a["cardio_load"] for a in hr) / hours if hours > 1 else 70.0,
            "climb_per_ft": sum(a["climb_load"] for a in recent) / gain if gain > 500 else 1.0,
            "descent_per_ft": sum(a["descent_load"] for a in recent) / loss if loss > 500 else 0.9}


def week_kinds(n: int) -> list[str]:
    """Build, recovery, taper and race weeks for a plan of n weeks, the last holding the objective.
    Recovery weeks are counted back from the taper, so the four weeks before it are all build:
    those are the weeks objective readiness reads."""
    tail = ["taper", "race"] if n >= 6 else ["race"]
    m = n - len(tail)
    kinds = ["recovery" if (m - 1 - i) % RECOVERY_EVERY == 0 and m - 1 - i > 0 and i > 0 else "build"
             for i in range(m)]
    return kinds + tail


class _Projector:
    """Carries the ledgers forward from today with planned daily loads, and reports any day that
    trips the ramp or overreach flag."""

    def __init__(self, history: list[LedgerDay]):
        # Only ledgers with enough history to have a normal: a ledger starting from nothing would
        # read any load as a ramp.
        by = {n: [d for d in history if d.ledger == n] for n in ledger.LEDGERS}
        self.hist = {n: h[-ledger.FITNESS_DAYS * 2:] for n, h in by.items()
                     if n in PLAN_LEDGERS or (len(h) >= ledger.MIN_HISTORY_DAYS and h[-1].fitness > 0)}

    def trial(self, days: list[tuple[date, dict]]) -> tuple[dict, list[tuple[str, str]]]:
        """Run a week of planned loads. A long day lifts the 7-day average for a few days by
        design, so the ramp limit is read on the week's mean of last 7 days against normal;
        overreach is not allowed on any day."""
        hist = {n: list(h) for n, h in self.hist.items()}
        bad = set()
        ratios: dict[str, list[float]] = {n: [] for n in hist}
        for day, loads in days:
            for n in hist:
                d = ledger._next(hist[n], n, day, loads.get(n, 0.0), seed=0.0)
                hist[n].append(d)
                if "overreach" in d.flags:
                    bad.add((n, "overreach"))
                if d.fitness > 0 and len(hist[n]) >= ledger.MIN_HISTORY_DAYS:
                    ratios[n].append(d.fatigue / d.fitness)
        for n, rs in ratios.items():
            if rs and sum(rs) / len(rs) > ledger.RAMP_RATIO:
                bad.add((n, "ramp"))
        return hist, sorted(bad)

    def commit(self, hist: dict) -> None:
        self.hist = {n: h[-ledger.FITNESS_DAYS * 2:] for n, h in hist.items()}

    def fitness(self, name: str) -> float:
        h = self.hist.get(name) or []
        return h[-1].fitness if h else 0.0


def sessions_for_week(target: dict, week_start: date, first_day: date, c: Constraints,
                      pace_min: float) -> tuple[list[dict], list[str]]:
    """Split a week's miles, gain and loss into seven days: a long day, one hill day and easy
    days, each held to that day's time limit. Days before `first_day` and days the athlete
    can't train are rest. Returns the days and a note for each day a time limit cut."""
    days = [week_start + timedelta(days=i) for i in range(7)]
    avail = [d for d in days if d.weekday() in c.days and d >= first_day]
    long_day = next((d for d in avail if d.weekday() == c.long_day), None)
    if len(avail) == 7:  # keep one rest day, the day after the long one
        after = long_day + timedelta(days=1) if long_day else None
        avail.remove(after if after in avail else avail[-1])
    others = [d for d in avail if d != long_day]
    hills = (max(others, key=lambda d: abs((d - long_day).days) if long_day else 0)
             if len(others) >= 2 else None)
    weights: dict[date, dict[str, float]] = {}
    long_share = {k: (1.0 if not others else max(LONG_SHARE[k], 1 / len(avail))) if long_day else 0.0
                  for k in DIMS}
    if long_day:
        weights[long_day] = dict(long_share)
    vert = {d: 2.0 if d == hills else 1.0 for d in others}
    for d in others:
        weights[d] = {"miles": (1 - long_share["miles"]) / len(others),
                      "gain_ft": (1 - long_share["gain_ft"]) * vert[d] / sum(vert.values()),
                      "loss_ft": (1 - long_share["loss_ft"]) * vert[d] / sum(vert.values())}
    # Fill days in proportion to their weights; a day over its time limit is held there and
    # what it couldn't take moves to the days that still have room.
    alloc = {d: {k: 0.0 for k in DIMS} for d in weights}
    left = {k: target[k] for k in DIMS}
    open_days = set(weights)
    capped: set[date] = set()
    for _ in range(len(weights) + 1):
        if not open_days or all(v <= 1e-6 for v in left.values()):
            break
        total_w = {k: sum(weights[d][k] for d in open_days) for k in DIMS}
        for d in open_days:
            for k in DIMS:
                if total_w[k] > 0:
                    alloc[d][k] += left[k] * weights[d][k] / total_w[k]
        left = dict.fromkeys(DIMS, 0.0)
        for d in list(open_days):
            minutes = effort_miles(alloc[d]["miles"], alloc[d]["gain_ft"]) * pace_min
            if minutes > c.cap(d):
                cut = c.cap(d) / minutes
                for k in DIMS:
                    left[k] += alloc[d][k] * (1 - cut)
                    alloc[d][k] *= cut
                open_days.discard(d)
                capped.add(d)
    out = []
    for d in days:
        if d not in weights:
            out.append({"day": d.isoformat(), "kind": "rest", "miles": 0.0, "gain_ft": 0.0, "loss_ft": 0.0,
                        "minutes": 0.0, "capped": False})
            continue
        kind = "long" if d == long_day else "hills" if d == hills else "easy"
        out.append({"day": d.isoformat(), "kind": kind, **alloc[d],
                    "minutes": effort_miles(alloc[d]["miles"], alloc[d]["gain_ft"]) * pace_min,
                    "capped": d in capped})
    short = any(v > 0.5 for v in left.values()) and not open_days
    by_cap: dict[int, list[str]] = {}
    for d in sorted(capped):
        by_cap.setdefault(c.cap(d), []).append(DAY_NAMES[d.weekday()])
    notes = [f"{_join(names)} {'are' if len(names) > 1 else 'is'} held to your {cap} min limit"
             for cap, names in by_cap.items()]
    if short:
        notes.append("The week doesn't fit in your available time, so it comes in under target")
    return out, notes


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _sum(sessions: list[dict]) -> dict:
    return {k: sum(s[k] for s in sessions) for k in (*DIMS, "minutes")}


def _in_week(day: date, start: date) -> bool:
    return start <= day < start + timedelta(days=7)


def _before(c: Constraints, day: date) -> Constraints:
    """The athlete's days, but only those before the objective in its week."""
    return Constraints(**{**asdict(c), "days": [d for d in c.days if d < day.weekday()]})


def _long_descent(obj: Objective, long_loss: float) -> float | None:
    """The continuous descent a long day should include, shaped like the objective's longest."""
    if not obj.longest_descent_ft:
        return None
    return min(long_loss, obj.longest_descent_ft * DURABILITY_TARGET)


def build(obj: Objective, acts: list[dict], history: list[LedgerDay], c: Constraints, today: date) -> dict:
    """Weekly targets from this week to the objective's week. See the module docstring."""
    if obj.day <= today:
        raise PlanError("Pick an objective date after today.")
    if not c.days:
        raise PlanError("Pick at least one day you can train.")
    p = pace(acts, today)
    pace_min = p["min_per_effort_mi"]
    dem = demand(obj, p)
    base = baseline(acts, today)
    r = rates(acts, today)
    week0 = today - timedelta(days=today.weekday())
    n = (obj.day - week0).days // 7 + 1
    kinds = week_kinds(n)
    long_target = {"miles": obj.distance_mi * LONG_TARGET, "gain_ft": obj.gain_ft * LONG_TARGET,
                   "loss_ft": obj.loss_ft * LONG_TARGET}
    peak = {"miles": max(base["miles"], long_target["miles"] / LONG_SHARE["miles"]),
            "gain_ft": max(base["gain_ft"], obj.gain_ft * VOLUME_TARGET, long_target["gain_ft"] / LONG_SHARE["gain_ft"]),
            "loss_ft": max(base["loss_ft"], obj.loss_ft * VOLUME_TARGET, long_target["loss_ft"] / LONG_SHARE["loss_ft"])}
    done = [a for a in acts if week0.isoformat() <= a["day"] <= today.isoformat() and is_foot(a["sport"])]
    done_today = any(a["day"] == today.isoformat() for a in done)
    done_sum = {"miles": sum(a["distance_mi"] for a in done), "gain_ft": sum(a["gain_ft"] for a in done),
                "loss_ft": sum(a["loss_ft"] for a in done), "minutes": sum(a["moving_time_s"] for a in done) / 60}

    first_planned = today + timedelta(days=1 if done_today else 0)
    proj = _Projector([d for d in history if d.day < first_planned])  # the plan takes over from there
    descent_now = _Projector(history).fitness("descent")
    level = {k: base[k] for k in DIMS}
    weeks: list[dict] = []
    descent_fitness_at: dict[int, float] = {}
    for i, kind in enumerate(kinds):
        start = week0 + timedelta(weeks=i)
        descent_fitness_at[i] = proj.fitness("descent")
        if kind == "build" and i > 0:
            want = {k: level[k] if level[k] >= peak[k] else min(peak[k], max(level[k], 1.0) * BUILD_RATE)
                    for k in DIMS}
        else:
            want = {k: level[k] * {"build": 1.0, "recovery": RECOVERY, "taper": TAPER, "race": RACE_WEEK}[kind]
                    for k in DIMS}
        first = first_planned if i == 0 else start
        planned = {k: max(0.0, want[k] - done_sum[k]) for k in DIMS} if i == 0 else want
        race = _in_week(obj.day, start)
        sessions, notes = sessions_for_week(planned, start, first, _before(c, obj.day) if race else c, pace_min)
        if race:
            for s in sessions:
                if s["kind"] == "long":
                    s["kind"] = "easy"

        def day_loads(scale: float) -> list[tuple[date, dict]]:
            return [(date.fromisoformat(s["day"]),
                     {"cardio": s["minutes"] * scale / 60 * r["cardio_per_hour"],
                      "distance": s["miles"] * scale,
                      "climb": s["gain_ft"] * scale * r["climb_per_ft"],
                      "descent": s["loss_ft"] * scale * r["descent_per_ft"]})
                    for s in sessions if date.fromisoformat(s["day"]) >= first]

        scale = 1.0
        hist, bad = proj.trial(day_loads(scale))
        limited_by = sorted({b[0] for b in bad})
        while bad and scale > MIN_CUT:
            scale = round(scale * CUT_STEP, 4)
            hist, bad = proj.trial(day_loads(scale))
        if scale < 1:
            for s in sessions:
                for k in (*DIMS, "minutes"):
                    s[k] *= scale
            notes.insert(0, f"Cut to {round(scale * 100)}% to stay inside the {_join(limited_by)} load limit")
        proj.commit(hist)
        if race:
            sessions[obj.day.weekday()] = {"day": obj.day.isoformat(), "kind": "objective",
                                           "miles": obj.distance_mi, "gain_ft": obj.gain_ft,
                                           "loss_ft": obj.loss_ft, "minutes": dem["time"]["mid_h"] * 60,
                                           "capped": False}
        long = next((s for s in sessions if s["kind"] == "long"), None)
        if long is not None:
            long["longest_descent_ft"] = _long_descent(obj, long["loss_ft"])
        total = _sum([s for s in sessions if s["kind"] != "objective"])
        if i == 0:
            total = {k: total[k] + done_sum[k] for k in total}
        limited = scale < 1 or any(s["capped"] for s in sessions)
        if kind == "build":
            if i == 0:
                level = {k: want[k] * scale for k in DIMS}
            else:
                level = {k: total[k] for k in DIMS} if limited else want
        focus = "peak" if kind == "build" and i > 0 and all(level[k] >= peak[k] * 0.98 for k in DIMS) else kind
        hours = total["minutes"] / 60
        weeks.append({
            "index": i, "start": start.isoformat(), "focus": focus,
            "miles": total["miles"], "gain_ft": total["gain_ft"], "loss_ft": total["loss_ft"],
            "hours": hours, "below_aet_hours": hours * 0.8,
            "long": None if long is None else {
                "day": long["day"], "miles": long["miles"], "gain_ft": long["gain_ft"], "loss_ft": long["loss_ft"],
                "minutes": long["minutes"], "effort_mi": effort_miles(long["miles"], long["gain_ft"]),
                "longest_descent_ft": long["longest_descent_ft"]},
            "limited_by": limited_by if scale < 1 else [], "notes": list(dict.fromkeys(notes)),
        })
    # Projected readiness, read at the start of the taper (or the race week): the last 4 weeks
    # before it and the biggest long day in the 8 weeks before it.
    cut = next(i for i, k in enumerate(kinds) if k in ("taper", "race"))
    before = weeks[max(0, cut - 4):cut] or weeks[:1]
    window = weeks[max(0, cut - 8):cut] or weeks[:1]
    longs = [w["long"] for w in window if w["long"]]
    current = capacity(acts, descent_now, today)
    overlap = (obj.day - today).days <= CAPACITY_DAYS  # today's biggest days still count on the day

    def best(values: list[float], key: str) -> float:
        return max(values + ([current[key]] if overlap else []), default=0.0)

    proj_cap = {
        "big_day": best([l["effort_mi"] for l in longs], "big_day"),
        "time_on_feet": best([l["minutes"] / 60 for l in longs], "time_on_feet"),
        "climbing_volume": sum(w["gain_ft"] for w in before) / len(before),
        "descent_durability": best([l["longest_descent_ft"] or 0.0 for l in longs], "descent_durability"),
        "descent_volume": descent_fitness_at.get(cut, descent_now) * 7,
        "altitude": current["altitude"],
    }
    projected = evaluate(dem, proj_cap)
    return {"objective": obj.json(), "built_on": today.isoformat(), "constraints": asdict(c),
            "baseline": base, "rates": r, "pace": p, "demand": dem, "peak": peak, "weeks": weeks,
            "projected": {"capacity": proj_cap, **projected}, "reachable": projected["verdict"] != "gap"}


def options(obj: Objective, acts: list[dict], history: list[LedgerDay], c: Constraints, today: date,
            plan: dict) -> dict:
    """When the objective is out of reach: a smaller version on the same date, and the earliest
    later date with no gap. Neither loosens a limit."""
    if plan["reachable"]:
        return {"smaller": None, "later": None}
    factor = 1.0
    for d in plan["projected"]["dims"]:
        if d["status"] == "gap" and d["demand"]:
            factor = min(factor, d["capacity"] / (d["rule"] * d["demand"]))
    factor = max(0.1, factor)
    smaller = {"factor": factor, "distance_mi": obj.distance_mi * factor, "gain_ft": obj.gain_ft * factor,
               "loss_ft": obj.loss_ft * factor}
    later = None
    for k in range(1, MAX_LATER_WEEKS + 1):
        moved = Objective(**{**asdict(obj), "day": obj.day + timedelta(weeks=k)})
        if build(moved, acts, history, c, today)["reachable"]:
            later = moved.day.isoformat()
            break
    return {"smaller": smaller, "later": later}


def plan_for_day(plan: dict, day: date) -> dict | None:
    """The plan week holding `day`."""
    for w in plan["weeks"]:
        start = date.fromisoformat(w["start"])
        if start <= day < start + timedelta(days=7):
            return w
    return None


OWN_LONG_SHARE = 0.80  # an own run this big, against the week's long day, is the long day
MIN_SESSION_MI, MIN_SESSION_FT = 2.0, 500.0  # a session smaller than both is rest


def _around_own(c: Constraints, mine: dict[str, dict], week: dict) -> Constraints:
    """The week's constraints with the athlete's own run days taken out. If one of their runs is
    the week's long day, the plan adds no other; if their run sits on the long day, the long day
    moves to the nearest free day, a weekend day first."""
    taken = {date.fromisoformat(d).weekday() for d in mine}
    free = [d for d in c.days if d not in taken]
    long_effort = (week.get("long") or {}).get("effort_mi") or 0.0
    has_long = any(r["kind"] in ("long", "race") or
                   (long_effort and effort_miles(r["miles"], r["gain_ft"]) >= OWN_LONG_SHARE * long_effort)
                   for r in mine.values())
    long_day = c.long_day
    if has_long:
        long_day = -1
    elif long_day in taken:
        long_day = min(free, key=lambda d: (d < 5, abs(d - c.long_day), -d), default=-1)
    return replace(c, days=free, long_day=long_day)


def _own_kind(kind: str) -> str:
    return "long" if kind in ("long", "race") else "hills" if kind == "hills" else "easy"


def upcoming_sessions(plan: dict, acts: list[dict], today: date, days: int = 7,
                      own: dict[str, dict] | None = None) -> list[dict]:
    """Dated sessions for the next `days` days, cut fresh from each week's targets less what is
    already done that week. Only these days get sessions: later weeks stay as targets, because a
    list of fixed sessions weeks out is wrong by the second week.

    `own` holds the athlete's own scheduled runs by ISO day. They count toward the week the same
    as what's done: the plan spreads only what's left over the other days, and an own run on a
    day takes that day (marked `own`)."""
    own = own or {}
    c = Constraints.from_json(plan["constraints"])
    obj = plan["objective"]
    obj_day = date.fromisoformat(obj["day"])
    pace_min = plan["pace"]["min_per_effort_mi"]
    foot = [a for a in acts if is_foot(a["sport"])]
    done_today = any(a["day"] == today.isoformat() for a in foot)
    first = today + timedelta(days=1) if done_today else today
    weeks: dict[str, dict[str, dict]] = {}
    out: list[dict] = []
    for k in range(days):
        day = today + timedelta(days=k)
        w = plan_for_day(plan, day)
        if w is None or day > obj_day:
            break
        if w["start"] not in weeks:
            start = date.fromisoformat(w["start"])
            done = [a for a in foot if w["start"] <= a["day"] <= today.isoformat()]
            mine = {d: r for d, r in own.items()
                    if _in_week(date.fromisoformat(d), start) and d >= first.isoformat() and d != obj["day"]}
            left = {d: max(0.0, w[d] - sum(a[{"miles": "distance_mi"}.get(d, d)] for a in done)
                           - sum(r[d] for r in mine.values())) for d in DIMS}
            race = _in_week(obj_day, start)
            cw = _around_own(_before(c, obj_day) if race else c, mine, w)
            ss, _ = sessions_for_week(left, start, max(first, start), cw, pace_min)
            for s in ss:
                r = mine.get(s["day"])
                if r is not None:
                    s.update(kind=_own_kind(r["kind"]), miles=r["miles"], gain_ft=r["gain_ft"], loss_ft=r["loss_ft"],
                             minutes=r.get("minutes") or effort_miles(r["miles"], r["gain_ft"]) * pace_min, own=True)
                elif s["kind"] != "rest" and s["miles"] < MIN_SESSION_MI and s["gain_ft"] < MIN_SESSION_FT:
                    # What's done and your own runs all but cover the week: a scrap of a run isn't worth it.
                    s.update(kind="rest", miles=0.0, gain_ft=0.0, loss_ft=0.0, minutes=0.0)
            for s in ss:
                if s["kind"] == "long" and not s.get("own"):
                    if race:
                        s["kind"] = "easy"
                    elif w.get("long") and w["long"].get("longest_descent_ft"):
                        s["longest_descent_ft"] = min(s["loss_ft"], w["long"]["longest_descent_ft"])
            weeks[w["start"]] = {s["day"]: s for s in ss}
        s = dict(weeks[w["start"]][day.isoformat()])
        if day == obj_day:
            s = {"day": day.isoformat(), "kind": "objective", "miles": obj["distance_mi"],
                 "gain_ft": obj["gain_ft"], "loss_ft": obj["loss_ft"],
                 "minutes": plan["demand"]["time"]["mid_h"] * 60}
        elif day < first:
            s = {"day": day.isoformat(), "kind": "done", "miles": 0.0, "gain_ft": 0.0, "loss_ft": 0.0, "minutes": 0.0}
        out.append(s | {"focus": w["focus"]})
    return out


SESSION_TITLE = {"long": "Long day", "hills": "Hills", "easy": "Easy aerobic", "rest": "Rest",
                 "objective": "Objective", "done": "Done for today"}


def session_text(s: dict, aet: float, objective_name: str | None = None) -> dict:
    """The words for a session: a title, how to run it, and the full detail with its numbers.
    Every number is the engine's."""
    kind = s["kind"]
    if kind == "rest":
        return {"title": "Rest", "how": "Rest, or an easy walk.", "detail": "Rest, or an easy walk."}
    if kind == "done":
        return {"title": "Done for today", "how": "Today's training is in. Recover.",
                "detail": "Today's training is in. Recover."}
    vol = f"{s['miles']:.1f} mi, {round(s['gain_ft'], -1):,.0f} ft up, {round(s['loss_ft'], -1):,.0f} ft down"
    if kind == "objective":
        return {"title": objective_name or "Objective", "how": "Go well.", "detail": f"{vol}. Go well."}
    mins = f"about {round(s['minutes'] / 5) * 5:.0f} min"
    if kind == "long":
        how = f"Keep it under {aet:.0f} bpm; hike the steep climbs."
        if s.get("longest_descent_ft"):
            how += f" Include one continuous descent of about {round(s['longest_descent_ft'], -2):,.0f} ft."
        title = "Long day"
    elif kind == "hills":
        how, title = f"Climb steady under {aet:.0f} bpm and hike anything steep.", "Hills"
    else:
        how, title = f"Under {aet:.0f} bpm.", "Easy aerobic"
    return {"title": title, "how": how, "detail": f"{vol}, {mins}. {how}"}


# Weekly review and replanning ------------------------------------------------------------------

MISSED_SHARE = 0.70  # under 70% of target on two of miles, gain and loss is a missed week


def week_actual(acts: list[dict], start: date) -> dict:
    end = start + timedelta(days=6)
    week = [a for a in acts if start.isoformat() <= a["day"] <= end.isoformat()]
    foot = [a for a in week if is_foot(a["sport"])]
    zones = {z: sum(a["zone_minutes"][z] for a in week) for z in ("z1", "z2", "z3")}
    zone_total = sum(zones.values())
    return {"activities": len(week), "miles": sum(a["distance_mi"] for a in foot),
            "gain_ft": sum(a["gain_ft"] for a in foot), "loss_ft": sum(a["loss_ft"] for a in foot),
            "hours": sum(a["moving_time_s"] for a in week) / 3600,
            "below_aet_pct": zones["z1"] / zone_total if zone_total else None,
            "longest_day_effort_mi": max((a["effort_mi"] for a in foot), default=0.0)}


def weekly_review(start: date, acts: list[dict], plan: dict | None, ledgers: list[LedgerDay],
                  pains: list[int]) -> dict:
    """Last week against its targets, the ledger flags it raised, and whether to replan."""
    end = start + timedelta(days=6)
    actual = week_actual(acts, start)
    target = plan_for_day(plan, start) if plan else None
    share = None
    if target:
        share = {k: (actual[k] / target[k] if target[k] else None) for k in DIMS}
    in_week = [d for d in ledgers if start <= d.day <= end and d.ledger in PLAN_LEDGERS]
    flags = sorted({(d.ledger, f) for d in in_week for f in d.flags})
    at_end = {d.ledger: d for d in in_week if d.day == end}
    reasons = []
    if share and sum(1 for k in DIMS if share[k] is not None and share[k] < MISSED_SHARE) >= 2:
        reasons.append("missed")
    if any(f in ("ramp", "overreach") for d in at_end.values() for f in d.flags):
        reasons.append("flag")
    if any(p > 3 for p in pains):
        reasons.append("pain")
    return {"start": start.isoformat(), "end": end.isoformat(), "actual": actual,
            "target": None if not target else {k: target[k] for k in (*DIMS, "hours", "focus")},
            "share": share,
            "flags": [{"ledger": n, "key": f, "text": ledger.FLAG_TEXT[f]} for n, f in flags],
            "vs_normal_at_end": {n: d.vs_normal for n, d in at_end.items()},
            "max_pain": max(pains, default=None), "replan": reasons}


REPLAN_TEXT = {
    "missed": "Last week came in well under its targets",
    "flag": "A load is ramping too fast",
    "pain": "You reported pain above 3 of 5",
    "objective": "The objective changed",
    "constraints": "Your training days or time limits changed",
    "new": "New objective",
    "request": "You asked for a fresh plan",
}


def diff(old: dict | None, new: dict) -> list[dict]:
    """Weeks whose targets moved by more than 5%, or whose focus changed, from old to new."""
    if old is None:
        return []
    before = {w["start"]: w for w in old["weeks"]}
    out = []
    for w in new["weeks"]:
        o = before.get(w["start"])
        if o is None:
            out.append({"start": w["start"], "before": None, "after": _brief(w)})
            continue
        moved = any(abs(w[k] - o[k]) > 0.05 * max(o[k], 1.0) for k in DIMS)
        if moved or w["focus"] != o["focus"]:
            out.append({"start": w["start"], "before": _brief(o), "after": _brief(w)})
    return out


def _brief(w: dict) -> dict:
    return {k: w[k] for k in ("focus", *DIMS, "hours")}
