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
FADE_MAX = 0.20  # at most 20% slower: past about 13 h the pace holds (hundreds would otherwise read 50% slower)
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
# Back-to-back long days: for an objective of B2B_FROM_H or more, the week's long day becomes a
# pair on consecutive days instead of one huge day. A pair counts toward readiness as one day of
# B2B_CREDIT times its combined size, when the smaller day is at least B2B_MIN of the bigger.
B2B_FROM_H = 7.0
B2B_CREDIT = 0.85
B2B_MIN = 0.33
B2B_SPLIT = 0.60  # the first day's share of the pair
PAIR_SHARE = {"miles": 0.55, "gain_ft": 0.6, "loss_ft": 0.6}  # the pair's share of the week
# Speed work, one session a week in build, peak and taper weeks when the athlete asks for it.
WORKOUTS = {"track": "Track intervals", "tempo": "Tempo", "hill_repeats": "Hill repeats", "race_pace": "Race pace",
            "threshold": "Threshold intervals", "fartlek": "Fartlek", "progression": "Progression run"}
WORKOUT_VERT = {"track": 0.0, "tempo": 0.5, "hill_repeats": 2.0, "race_pace": 0.3,
                "threshold": 0.3, "fartlek": 0.7, "progression": 0.7}  # the day's vert weight; easy is 1
# The week's hill day: one kind of mountain session, rotating through a build so each gets trained.
HILL_SESSIONS = {"vert_climb": "Long climb", "power_hike": "Power-hike intervals", "downhill": "Downhill durability",
                 "muscular_endurance": "Muscular endurance", "hill_sprints": "Hill sprints"}
HILL_ROTATION = ("vert_climb", "power_hike", "downhill")
WORKOUT_CARDIO = 1.2  # a speed session's cardio load per hour, against an easy hour
HOLD_ABOVE = 1.25
# Race types. The objective's shape picks how the plan trains: a flat race up to a marathon is a
# road race, 90 mi and up a hundred, 150 mi and up a multi-day race, and the rest mountain days.
ROAD_MAX_MI = 27.0
ROAD_FT_PER_MI = 60.0
HUNDRED_MI = 90.0
MULTIDAY_MI = 150.0
RACE_TYPES = {"mountain": "Mountain day", "road": "Road race", "hundred": "100-mile race",
              "multiday": "Multi-day race"}
# Hundreds and multi-day races can't be trained with a long day near their size: the long pair
# is a smaller share of the race, and weekly vert a smaller share of its total. Readiness rules
# for big-day size, time on feet and vert volume scale with them.
LONG_TARGET_BY_TYPE = {"hundred": 0.36, "multiday": 0.20}
VOLUME_TARGET_BY_TYPE = {"hundred": 0.60, "multiday": 0.35}
# Road races: the long run by race distance (5k, 10k, half, marathon), interpolated between,
# carrying a third of the week's miles; the week's quality session defaults by distance.
ROAD_LONG_MI = ((3.1, 7.0), (6.2, 9.0), (13.1, 13.0), (26.2, 20.0))
ROAD_LONG_SHARE = 0.33
NIGHT_EVERY = 3  # hundreds and multi-day races: a night long day every third build week
# Taper weeks before the race week, with each one's share of the build level.
TAPERS = {1: (0.70,), 2: (0.80, 0.60)}


# Goals. A race or a trip has a day to be ready for; the rest are blocks of weekly targets.
GOAL_KINDS = {"race": "Race", "trip": "Mountain objective", "base": "Build base", "maintain": "Maintain fitness",
              "comeback": "Come back from a break", "faster": "Get faster", "target": "Yearly target",
              "rolling": "Ongoing plan"}
EVENT_GOALS = ("race", "trip")
BLOCK_WEEKS = 12  # a block with no end of its own
ROLLING_WEEKS = 8  # an ongoing plan looks this far ahead and is redrawn each week
COMEBACK_EVERY = 3  # coming back: a recovery week after every two build weeks
COMEBACK_START = 0.30  # coming back from little or nothing: the first week is 30% of the target
BASE_GROWTH = 1.30  # build base: 30% past the last four weeks
ROLLING_GROWTH = 1.10
FASTER_VERT = 0.80  # get faster: a little less vert, to make room for the speed session


def is_event(obj: Objective | dict) -> bool:
    goal = obj.get("goal", "race") if isinstance(obj, dict) else obj.goal
    return goal in EVENT_GOALS


def block_kinds(n: int, every: int = RECOVERY_EVERY) -> list[str]:
    """Build and recovery weeks for a block with no event: a recovery week every `every` weeks."""
    return ["recovery" if i > 0 and (i + 1) % every == 0 else "build" for i in range(n)]


def year_done(acts: list[dict], today: date) -> dict:
    """Miles, gain and loss on foot so far this calendar year, through yesterday."""
    first = date(today.year, 1, 1).isoformat()
    year = [a for a in acts if first <= a["day"] < today.isoformat() and is_foot(a["sport"])]
    return {"miles": sum(a["distance_mi"] for a in year), "gain_ft": sum(a["gain_ft"] for a in year),
            "loss_ft": sum(a["loss_ft"] for a in year)}


def block_weekly(obj: Objective, acts: list[dict], base: dict, today: date) -> dict:
    """A block's weekly targets. A yearly target spreads what's left of it over the weeks left;
    a total not set holds the last four weeks."""
    weekly = {"miles": obj.distance_mi, "gain_ft": obj.gain_ft, "loss_ft": obj.loss_ft}
    if obj.goal != "target":
        return weekly
    done = year_done(acts, today)
    weeks_left = max(1.0, ((obj.day - today).days + 1) / 7)
    return {k: max(0.0, weekly[k] - done[k]) / weeks_left if weekly[k] else base[k] for k in DIMS}


def race_type(distance_mi: float, gain_ft: float) -> str:
    if distance_mi >= MULTIDAY_MI:
        return "multiday"
    if distance_mi >= HUNDRED_MI:
        return "hundred"
    if distance_mi <= ROAD_MAX_MI and gain_ft <= ROAD_FT_PER_MI * distance_mi:
        return "road"
    return "mountain"


def road_long_mi(distance_mi: float) -> float:
    pts = ROAD_LONG_MI
    if distance_mi <= pts[0][0]:
        return pts[0][1]
    for (d0, l0), (d1, l1) in zip(pts, pts[1:]):
        if distance_mi <= d1:
            return l0 + (l1 - l0) * (distance_mi - d0) / (d1 - d0)
    return pts[-1][1]


def road_workout(distance_mi: float) -> str:
    """The default quality session for a road race: track for a 5k or 10k, tempo for a half,
    race-pace miles for a marathon."""
    return "track" if distance_mi <= 6.5 else "tempo" if distance_mi <= 13.5 else "race_pace"


def taper_weeks(rtype: str, distance_mi: float) -> int:
    return 2 if rtype in ("hundred", "multiday") or (rtype == "road" and distance_mi > 20) else 1


def rule_scale(rtype: str) -> dict:
    """How much each readiness rule shrinks for a race type: 1 for all but hundreds and multi-day."""
    big = LONG_TARGET_BY_TYPE.get(rtype, LONG_TARGET) / LONG_TARGET
    vol = VOLUME_TARGET_BY_TYPE.get(rtype, VOLUME_TARGET) / VOLUME_TARGET
    return {"big_day": big, "time_on_feet": big, "climbing_volume": vol, "descent_volume": vol}


# Plan types, the kind of plan the athlete is told they're getting, from the objective's shape.
# A race past a marathon is an ultra; one climbing MOUNTAIN_FT_PER_MI or more a mile, a mountain ultra.
MOUNTAIN_FT_PER_MI = 100.0
PLAN_TYPES = {
    "fast_5k": ("Fast 5k and 10k plan", "speed on easy miles: track and threshold intervals, strides and short hill "
                                       "sprints, with a short taper"),
    "half": ("Half marathon plan", "tempo and threshold work on easy miles, a long run building toward the race "
                                   "distance, and a taper"),
    "marathon": ("Marathon plan", "a long run building toward 20 mi, tempo and race-pace miles, easy miles with "
                                  "little vert, and a two-week taper"),
    "trail": ("Trail race plan", "long days on terrain like the race's, a weekly hill day and steady climbing volume"),
    "mountain_day": ("Mountain objective plan", "long days in the mountains, a weekly hill day, power-hiking and the "
                                                "climbing and descending the day needs"),
    "ultra": ("Ultra plan", "long runs for time on feet, back-to-back long days when the race is long enough, steady "
                            "aerobic miles and practice eating on the move"),
    "mountain_ultra": ("Mountain ultra plan", "long mountain days with big climbs and descents, power-hiking, "
                                              "downhill durability and leg strength"),
    "hundred": ("100-mile plan", "long days well short of the race that practice power-hiking, race food and moving "
                                 "in the dark, then a two-week taper"),
    "multiday": ("Multi-day plan", "long days well short of the race that practice power-hiking, race food and the "
                                   "dark with a short sleep stop, then a two-week taper"),
}


def plan_type(obj: Objective | dict) -> str:
    """The kind of plan an objective gets: a PLAN_TYPES key for a race or trip, the goal itself
    for a block with no race day."""
    o = obj.json() if isinstance(obj, Objective) else obj
    goal = o.get("goal", "race")
    if goal not in EVENT_GOALS:
        return goal
    d, g = float(o["distance_mi"]), float(o["gain_ft"])
    rtype = race_type(d, g)
    if rtype == "road":
        return "fast_5k" if d <= 6.5 else "half" if d <= 13.5 else "marathon"
    if rtype in ("hundred", "multiday"):
        return rtype
    if d > ROAD_MAX_MI:
        return "mountain_ultra" if g >= MOUNTAIN_FT_PER_MI * d else "ultra"
    return "mountain_day" if goal == "trip" else "trail"


def plan_type_text(obj: Objective | dict) -> dict:
    """The plan type's key, its name and what it trains, for the athlete."""
    key = plan_type(obj)
    if key in PLAN_TYPES:
        label, text = PLAN_TYPES[key]
    else:
        label, text = f"{GOAL_KINDS.get(key, 'Training')} plan", BLOCK_TYPE_TEXT.get(key, "")
    return {"key": key, "label": label, "text": text}


BLOCK_TYPE_TEXT = {
    "base": "easy aerobic miles and climbing built up gently, with a recovery week every fourth week",
    "maintain": "your current miles and vert held steady, with a lighter week every fourth week",
    "comeback": "an easy start building back to where you were, with a lighter week every third week",
    "faster": "a weekly speed session on near your current miles, with a little less vert",
    "target": "the weekly miles and vert that reach the year's target, built up inside the load limits",
    "rolling": "a few weeks at a time, redrawn each Monday from your latest training",
}

# The workouts an athlete can ask for: speed sessions and hill sessions rotate through the build;
# strides and strength are the engine's extras on easy days. Defaults by plan type: what that
# kind of plan usually has.
EXTRAS = {"strides": "Strides", "strength": "Strength", "back_to_back": "Back-to-back long days"}
DEFAULT_SPEED = {"fast_5k": ["track", "threshold"], "half": ["tempo", "threshold"], "marathon": ["race_pace", "tempo"],
                 "faster": ["tempo", "track"]}
DEFAULT_HILLS = {"fast_5k": ["hill_sprints"], "half": ["hill_sprints"], "marathon": ["hill_sprints"],
                 "faster": ["hill_sprints", "vert_climb"],
                 "mountain_ultra": ["vert_climb", "power_hike", "downhill", "muscular_endurance"],
                 "hundred": ["vert_climb", "power_hike", "downhill", "muscular_endurance"],
                 "multiday": ["vert_climb", "power_hike", "downhill", "muscular_endurance"]}


def default_workouts(ptype: str) -> dict:
    """The workouts a plan type starts with, before the athlete changes them."""
    return {"workouts": list(DEFAULT_SPEED.get(ptype, [])),
            "hill_sessions": list(DEFAULT_HILLS.get(ptype, list(HILL_ROTATION))),
            "strides": True, "strength": True, "back_to_back": ptype in ("ultra", "mountain_ultra", "hundred", "multiday")}


def pick_speed(c: Constraints, n: int, default: str | None) -> str | None:
    """The n-th speed session of the plan: the athlete's picks in turn, or, when they haven't
    picked, the one weekly session they asked for or the plan type's default."""
    if c.workouts is None:
        return c.workout if c.workout in WORKOUTS else default
    return c.workouts[n % len(c.workouts)] if c.workouts else None


def pick_hill(c: Constraints, hill: str, focus: str, n: int) -> str:
    """The week's hill session held to the athlete's picks: kept when picked, a steady climb in an
    easy week, otherwise the picks in turn. With none picked the hill day is a steady climb."""
    if c.hill_sessions is None or hill in c.hill_sessions:
        return hill
    if focus in ("recovery", "taper", "race") or not c.hill_sessions:
        return "vert_climb"
    return c.hill_sessions[n % len(c.hill_sessions)]


ADJUST_RANGE = (0.5, 1.2)  # an asked-for week change scales its targets within these
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
    # What the plan is for: a race or a mountain objective on a day (EVENT_GOALS), or a block with
    # no event in it. For a block, day is the block's last day; distance_mi, gain_ft and loss_ft are
    # weekly targets, except for "target", where they are the year's totals (0 for one not set).
    goal: str = "race"

    def json(self) -> dict:
        return asdict(self) | {"day": self.day.isoformat()}


MAX_WEEKDAY_MIN = 720  # 12 h on a weekday
MAX_WEEKEND_MIN = 1440  # a whole day on a weekend, for the longest ultra and multi-day builds


@dataclass
class Constraints:
    days: list[int] = field(default_factory=lambda: [1, 2, 3, 5, 6])  # 0 is Monday
    weekday_min: int = 90
    weekend_min: int = 360
    long_day: int = 5
    notes: str = ""
    back_to_back: bool = True  # pair the long day with a second one when the objective calls for it
    workout: str = "none"  # weekly speed session: none, track, tempo or hill_repeats
    # Asked-for changes to single weeks: {"week": ISO Monday, "dims": ["miles"|"vert"], "factor": f}.
    # Applied to that week's targets only, and still checked against the load limits.
    adjust: list = field(default_factory=list)
    # The workouts the athlete picked: speed sessions (WORKOUTS keys) and hill sessions
    # (HILL_SESSIONS keys), each rotating through the build. None: not picked, so `workout` and the
    # plan type's defaults decide; an empty list: none of that kind.
    workouts: list | None = None
    hill_sessions: list | None = None
    strides: bool = True
    strength: bool = True

    @classmethod
    def from_json(cls, d: dict | None) -> Constraints:
        c = cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})
        c.days = sorted({int(x) for x in c.days if 0 <= int(x) <= 6})
        c.adjust = [a for a in (c.adjust or []) if isinstance(a, dict) and a.get("week")]
        if c.workout not in WORKOUTS:
            c.workout = "none"
        if c.workouts is not None:
            c.workouts = list(dict.fromkeys(w for w in c.workouts if w in WORKOUTS))
        if c.hill_sessions is not None:
            c.hill_sessions = list(dict.fromkeys(h for h in c.hill_sessions if h in HILL_SESSIONS))
        c.weekday_min = min(max(int(c.weekday_min), 15), MAX_WEEKDAY_MIN)
        c.weekend_min = min(max(int(c.weekend_min), 15), MAX_WEEKEND_MIN)
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
    mid = base * (1 + min(FADE_MAX, FADE_PER_H * max(0.0, base - FADE_FROM_H)))
    return {"low_h": mid * TIME_RANGE[0], "mid_h": mid, "high_h": mid * TIME_RANGE[1]}


def demand(obj: Objective, p: dict) -> dict:
    effort = effort_miles(obj.distance_mi, obj.gain_ft)
    t = estimate_time(effort, p["min_per_effort_mi"])
    return {"distance_mi": obj.distance_mi, "gain_ft": obj.gain_ft, "loss_ft": obj.loss_ft, "effort_mi": effort,
            "race_type": race_type(obj.distance_mi, obj.gain_ft),
            "longest_climb_ft": obj.longest_climb_ft, "longest_descent_ft": obj.longest_descent_ft,
            "high_point_ft": obj.high_point_ft, "time": t | {"pace_learned": p["learned"]},
            "dims": {"big_day": effort, "time_on_feet": t["mid_h"], "climbing_volume": obj.gain_ft,
                     "descent_durability": obj.longest_descent_ft, "descent_volume": obj.loss_ft,
                     "altitude": obj.high_point_ft}}


def b2b_sized(dem: dict) -> bool:
    """An objective long enough to train for with back-to-back long days. Never a road race: a
    slow marathon still trains with one long run, not two shorter days."""
    return dem["time"]["mid_h"] >= B2B_FROM_H and dem.get("race_type") != "road"


def pair_size(first: float, second: float) -> float:
    """What two long days in a row count for as one big day, or 0 when they aren't a pair."""
    if min(first, second) < B2B_MIN * max(first, second) or not first:
        return 0.0
    return (first + second) * B2B_CREDIT


def _best_pair(acts: list[dict], key: str) -> float:
    by_day: dict[str, float] = {}
    for a in acts:
        by_day[a["day"]] = by_day.get(a["day"], 0.0) + a[key]
    return max((pair_size(v, by_day.get((date.fromisoformat(d) + timedelta(days=1)).isoformat(), 0.0))
                for d, v in by_day.items()), default=0.0)


def capacity(acts: list[dict], descent_fitness: float, day: date, pairs: bool = False) -> dict:
    """What training has proven by `day`, in the same terms as the demand. With `pairs`, a
    back-to-back pair of long days counts toward big-day size and time on feet."""
    last8, last4 = _within(acts, day, CAPACITY_DAYS), _within(acts, day, VOLUME_DAYS)
    big = max((a["effort_mi"] for a in last8), default=0.0)
    longest = max((a["moving_time_s"] for a in last8), default=0.0) / 3600
    if pairs:
        big = max(big, _best_pair(last8, "effort_mi"))
        longest = max(longest, _best_pair(last8, "moving_time_s") / 3600)
    return {"big_day": big,
            "time_on_feet": longest,
            "climbing_volume": sum(a["gain_ft"] for a in last4) / 4,
            "descent_durability": max((a.get("longest_descent_ft") or 0.0 for a in last8), default=0.0),
            "descent_volume": descent_fitness * 7,
            "altitude": sum(a.get("minutes_above_8000ft") or 0.0 for a in last4) / 60}


PAIR_LABEL = {"big_day": "Largest single day or back-to-back pair, last 8 weeks",
              "time_on_feet": "Longest single day or back-to-back pair, last 8 weeks"}


def evaluate(dem: dict, cap: dict, pairs: bool = False) -> dict:
    """Each dimension reads ready, close or gap; the limiter is the furthest behind its rule."""
    dims = []
    scale = rule_scale(dem.get("race_type", "mountain"))
    for key, name, dem_text, cap_text, rule, unit in DIMENSIONS:
        if rule is not None:
            rule = round(rule * scale.get(key, 1.0), 2)
        if pairs:
            cap_text = PAIR_LABEL.get(key, cap_text)
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
    pairs = b2b_sized(dem)
    cap = capacity(acts, descent_fitness, today, pairs)
    return {"demand": dem, "capacity": cap, "pace": p,
            "days_to_go": (obj.day - today).days, **evaluate(dem, cap, pairs)}


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


def week_kinds(n: int, tapers: int = 1) -> list[str]:
    """Build, recovery, taper and race weeks for a plan of n weeks, the last holding the objective.
    Recovery weeks are counted back from the taper, so the four weeks before it are all build:
    those are the weeks objective readiness reads. A plan too short for `tapers` taper weeks
    gets one, and one under 6 weeks none."""
    tail = (["taper"] * tapers + ["race"] if n >= 5 + tapers else ["taper", "race"] if n >= 6 else ["race"])
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


def _second_day(long_day: date | None, avail: list[date]) -> date | None:
    """The other half of a back-to-back pair: the day after the long day, or the day before."""
    if long_day is None:
        return None
    return next((d for d in (long_day + timedelta(days=1), long_day - timedelta(days=1)) if d in avail), None)


def _workout_day(workout: str | None, others: list[date], hills: date | None, pair: list[date]) -> date | None:
    """The day for the week's speed session: hill repeats take the hill day; track and tempo
    take the easy day furthest from the long day and the hill day, never the day before or after
    either when another day will do."""
    if not workout:
        return None
    if workout == "hill_repeats" and hills:
        return hills
    easy = [d for d in others if d != hills]
    if not easy:
        return None
    hard = pair + ([hills] if hills else [])
    return max(easy, key=lambda d: (min((abs((d - h).days) for h in hard), default=7), -d.toordinal()))


def default_hill_session(rtype: str, focus: str, builds: int, late: bool = False) -> str:
    """The hill day's session when no one picked one. Mountain builds rotate a long climb,
    power-hike intervals and downhill repeats; the last weeks before the taper (`late`) and peak
    weeks swap every other one for muscular endurance; recovery and taper weeks climb easy. A
    road race does short hill sprints for strength and form."""
    if rtype == "road":
        return "hill_sprints" if focus in ("build", "peak", "hold") else "vert_climb"
    if focus in ("recovery", "taper", "race"):
        return "vert_climb"
    if (late or focus == "peak") and builds % 2 == 0:
        return "muscular_endurance"
    return HILL_ROTATION[builds % len(HILL_ROTATION)]


def type_week(sessions: list[dict], week: dict, c: Constraints | None = None) -> None:
    """Give a week's days their kind of session beyond the split: the hill day's session, the
    recovery run after the long day, strides on one easy day, and strength on two, unless the
    athlete turned strides or strength off (`c`). In place."""
    focus = week.get("focus", "build")
    hill = week.get("hill_session") or "vert_climb"
    run = [s for s in sessions if s["kind"] not in ("rest", "done", "objective")]
    longs = [date.fromisoformat(s["day"]) for s in run if s["kind"] in ("long", "long2")]
    hard = set(longs) | {date.fromisoformat(s["day"]) for s in run if s["kind"] in WORKOUTS or s["kind"] == "hills"}
    after_long = (max(longs) + timedelta(days=1)).isoformat() if longs else None
    easy = [s for s in run if s["kind"] == "easy" and not s.get("own")]
    for s in run:
        if s["kind"] == "hills":
            s["type"] = hill
    for s in easy:
        if focus in ("recovery", "taper", "race") or s["day"] == after_long:
            s["type"] = "recovery"
    if focus in ("build", "peak", "hold") and (c is None or c.strides):
        # Strides on an easy day before nothing hard, as far from the long day as the week allows.
        fresh = [s for s in easy if "type" not in s and date.fromisoformat(s["day"]) + timedelta(days=1) not in hard]
        if fresh:
            max(fresh, key=lambda s: min((abs((date.fromisoformat(s["day"]) - d).days) for d in longs),
                                         default=7))["type"] = "strides"
    # Strength twice a week (once in a taper, none in the race week), first on easy days that
    # aren't the day before a hard one, so the legs come to the long day fresh.
    want = 0 if c is not None and not c.strength else {"race": 0, "taper": 1}.get(focus, 2)
    spots = [s for s in easy if date.fromisoformat(s["day"]) + timedelta(days=1) not in hard]
    spots += [s for s in easy if s not in spots]
    for s in spots[:want]:
        s["strength"] = True
    for s in run:
        s.setdefault("type", s["kind"])


def sessions_for_week(target: dict, week_start: date, first_day: date, c: Constraints,
                      pace_min: float, b2b: bool = False, workout: str | None = None,
                      road_long: float | None = None, hill_day: bool = True) -> tuple[list[dict], list[str]]:
    """Split a week's miles, gain and loss into seven days: a long day (with `b2b`, a pair of
    long days back to back), one hill day, at most one speed session (`workout`) and easy days,
    each held to that day's time limit. For a road race (`road_long`, the longest long run in
    miles) the long run takes a third of the week and never runs past that. Days before `first_day` and days the athlete can't train are rest. Returns the days and a
    note for each day a time limit cut."""
    days = [week_start + timedelta(days=i) for i in range(7)]
    avail = [d for d in days if d.weekday() in c.days and d >= first_day]
    long_day = next((d for d in avail if d.weekday() == c.long_day), None)
    second = _second_day(long_day, avail) if b2b else None
    if len(avail) == 7:  # keep one rest day, the day after the long one (or the pair)
        pair = [d for d in (long_day, second) if d]
        after = max(pair) + timedelta(days=1) if pair else None
        avail.remove(after if after in avail else next(d for d in avail if d not in pair))
    pair = [d for d in (long_day, second) if d]
    others = [d for d in avail if d not in pair]
    hills = (max(others, key=lambda d: abs((d - long_day).days) if long_day else 0)
             if len(others) >= 2 and hill_day else None)
    speed = _workout_day(workout, others, hills, pair)
    weights: dict[date, dict[str, float]] = {}
    share = PAIR_SHARE if second else LONG_SHARE | ({"miles": ROAD_LONG_SHARE} if road_long else {})
    long_share = {k: (1.0 if not others else max(share[k], len(pair) / len(avail))) if long_day else 0.0
                  for k in DIMS}
    if long_day:
        split = B2B_SPLIT if second else 1.0
        weights[long_day] = {k: v * split for k, v in long_share.items()}
    if second:
        weights[second] = {k: v * (1 - B2B_SPLIT) for k, v in long_share.items()}
    # Vert goes mostly to the hill day; a track session is flat and a tempo mostly so.
    vert = {d: 2.0 if d == hills else WORKOUT_VERT.get(workout, 1.0) if d == speed else 1.0 for d in others}
    for d in others:
        weights[d] = {"miles": (1 - long_share["miles"]) / len(others),
                      "gain_ft": (1 - long_share["gain_ft"]) * vert[d] / (sum(vert.values()) or 1),
                      "loss_ft": (1 - long_share["loss_ft"]) * vert[d] / (sum(vert.values()) or 1)}
    # Fill days in proportion to their weights; a day over its time limit is held there and
    # what it couldn't take moves to the days that still have room.
    # A road long run grows past its race distance's size only as its share of a bigger week.
    most_long = max(road_long or 0.0, share["miles"] * target["miles"])
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
            cut = c.cap(d) / minutes if minutes > c.cap(d) else 1.0
            if road_long and d == long_day and alloc[d]["miles"] > most_long:
                cut = min(cut, most_long / alloc[d]["miles"])
            if cut < 1.0:
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
        kind = ("long" if d == long_day else "long2" if d == second else workout if d == speed
                else "hills" if d == hills else "easy")
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


def build(obj: Objective, acts: list[dict], history: list[LedgerDay], c: Constraints, today: date,
          proposal: list[dict] | None = None) -> dict:
    """Weekly targets from this week to the objective's week. See the module docstring.

    `proposal`, from the AI coach, sets each week's focus, miles, gain, loss and speed session in
    place of the engine's own build. The engine still holds every week to its rules: no week adds
    more than BUILD_RATE over the last build week, and the load-limit cuts apply as always."""
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
    event = is_event(obj)
    rtype = dem["race_type"] if event else "mountain"
    kinds = (week_kinds(n, taper_weeks(rtype, obj.distance_mi)) if event
             else block_kinds(n, COMEBACK_EVERY if obj.goal == "comeback" else RECOVERY_EVERY))
    pairs = b2b_sized(dem) if event else False
    b2b = pairs and c.back_to_back
    lt = LONG_TARGET_BY_TYPE.get(rtype, LONG_TARGET)
    volume = VOLUME_TARGET_BY_TYPE.get(rtype, VOLUME_TARGET)
    long_target = {"miles": obj.distance_mi * lt, "gain_ft": obj.gain_ft * lt, "loss_ft": obj.loss_ft * lt}
    share = LONG_SHARE
    if rtype == "road":
        # A road long run is set by the race distance, and runs a third of the week's miles.
        f = road_long_mi(obj.distance_mi) / obj.distance_mi
        long_target = {k: v * f / lt for k, v in long_target.items()}
        share = LONG_SHARE | {"miles": ROAD_LONG_SHARE}
    # With back-to-back days the pair, not one day, carries the long-day target: big enough that
    # its readiness credit reaches the same size.
    if b2b:
        long_target = {k: v / B2B_CREDIT for k, v in long_target.items()}
        share = PAIR_SHARE
    need = {"miles": long_target["miles"] / share["miles"],
            "gain_ft": max(obj.gain_ft * volume, long_target["gain_ft"] / share["gain_ft"]),
            "loss_ft": max(obj.loss_ft * volume, long_target["loss_ft"] / share["loss_ft"])}
    default_speed = road_workout(obj.distance_mi) if rtype == "road" else None
    if proposal is not None:
        kinds = _proposed_kinds(proposal, n, week0, event)
    tapers = [i for i, k in enumerate(kinds) if k == "taper"]
    builds = speeds = 0
    # Build to what the objective needs. Already past it, hold no more than HOLD_ABOVE of it:
    # more volume than the day calls for only makes the long day oversized.
    peak = {k: max(need[k], min(base[k], need[k] * HOLD_ABOVE)) for k in DIMS}
    if not event:
        # A block builds to (or steps down to) its weekly targets and holds them.
        peak = block_weekly(obj, acts, base, today)
        if obj.goal == "faster":
            default_speed = "tempo"
    road_long = road_long_mi(obj.distance_mi) if rtype == "road" else None
    if road_long:
        peak["miles"] = max(need["miles"], base["miles"])  # more miles never hurt a road race; only vert steps down
    done = [a for a in acts if week0.isoformat() <= a["day"] <= today.isoformat() and is_foot(a["sport"])]
    done_today = any(a["day"] == today.isoformat() for a in done)
    done_sum = {"miles": sum(a["distance_mi"] for a in done), "gain_ft": sum(a["gain_ft"] for a in done),
                "loss_ft": sum(a["loss_ft"] for a in done), "minutes": sum(a["moving_time_s"] for a in done) / 60}

    first_planned = today + timedelta(days=1 if done_today else 0)
    proj = _Projector([d for d in history if d.day < first_planned])  # the plan takes over from there
    descent_now = _Projector(history).fitness("descent")
    level = {k: base[k] for k in DIMS}
    if obj.goal == "comeback":
        level = {k: max(base[k], peak[k] * COMEBACK_START) for k in DIMS}
    weeks: list[dict] = []
    descent_fitness_at: dict[int, float] = {}
    for i, kind in enumerate(kinds):
        start = week0 + timedelta(weeks=i)
        descent_fitness_at[i] = proj.fitness("descent")
        held: list[str] = []
        if proposal is not None:
            # The coach's week, held to BUILD_RATE over the last build level (no more than that level
            # for any other week).
            pw = proposal[i]
            cap = {k: max(level[k], 1.0) * (BUILD_RATE if kind == "build" else 1.0) for k in DIMS}
            want = {k: min(float(pw[k]), cap[k]) for k in DIMS}
            held = [k for k in DIMS if float(pw[k]) > cap[k] * 1.01]
        elif kind == "build" and i > 0:
            want = {k: peak[k] if level[k] >= peak[k] else min(peak[k], max(level[k], 1.0) * BUILD_RATE)
                    for k in DIMS}
        elif kind == "taper":
            f = TAPERS[len(tapers)][tapers.index(i)] if len(tapers) in TAPERS else TAPER
            want = {k: level[k] * f for k in DIMS}
        else:
            want = {k: level[k] * {"build": 1.0, "recovery": RECOVERY, "race": RACE_WEEK}[kind] for k in DIMS}
        first = first_planned if i == 0 else start
        planned = {k: max(0.0, want[k] - done_sum[k]) for k in DIMS} if i == 0 else dict(want)
        asked = [a for a in c.adjust if a["week"] == start.isoformat()]
        for a in asked:
            f = min(max(float(a.get("factor", 1.0)), ADJUST_RANGE[0]), ADJUST_RANGE[1])
            for k in DIMS:
                if ("miles" if k == "miles" else "vert") in a.get("dims", []):
                    planned[k] *= f
        race = event and _in_week(obj.day, start)
        own_pick = pick_speed(c, speeds, default_speed) if kind in ("build", "taper") else None
        speed = own_pick
        if proposal is not None:
            speed = proposal[i]["workout"] if proposal[i]["workout"] in WORKOUTS and kind != "race" else None
            if c.workouts is not None and speed is not None and speed not in c.workouts:
                speed = own_pick  # the coach picked a session the athlete didn't ask for
        speeds += speed is not None
        sessions, notes = sessions_for_week(planned, start, first, _before(c, obj.day) if race else c, pace_min,
                                            b2b and not race, speed, road_long)
        if held:
            notes.insert(0, f"The coach asked for more {_join(['miles' if k == 'miles' else 'gain' if k == 'gain_ft' else 'descent' for k in held])}; "
                            f"held to {round((BUILD_RATE - 1) * 100)}% over the last build week")
        for a in asked:
            what = _join(["miles" if d == "miles" else "vert" for d in a.get("dims", [])])
            notes.insert(0, f"{'Less' if float(a.get('factor', 1.0)) < 1 else 'More'} {what} this week, as you asked")
        if race:
            for s in sessions:
                if s["kind"] in ("long", "long2"):
                    s["kind"] = "easy"

        def day_loads(scale: float) -> list[tuple[date, dict]]:
            return [(date.fromisoformat(s["day"]),
                     {"cardio": s["minutes"] * scale / 60 * r["cardio_per_hour"]
                      * (WORKOUT_CARDIO if s["kind"] in WORKOUTS else 1.0),
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
        second = next((s for s in sessions if s["kind"] == "long2"), None)
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
                # A week you asked to change doesn't move the build: the next week carries on.
                level = {k: total[k] for k in DIMS} if limited and not asked else want
        focus = ("peak" if event else "hold") if kind == "build" and i > 0 and all(
            level[k] >= peak[k] * 0.98 for k in DIMS) else kind
        if proposal is not None:
            pf = proposal[i]["focus"]
            focus = pf if kind == "build" and pf in ("build", "peak", "hold") else kind
        builds += kind == "build"
        # Hundreds and multi-day races: every third build week's long day runs into the night.
        night = (rtype in ("hundred", "multiday") and kind == "build" and long is not None
                 and builds % NIGHT_EVERY == 0)
        if night:
            notes.append("Run part of the long day in the dark: headlamp, night pace and eating on the move"
                         + (", plus a 20 min sleep stop" if rtype == "multiday" else ""))
        hours = total["minutes"] / 60
        weeks.append({
            "index": i, "start": start.isoformat(), "focus": focus,
            "miles": total["miles"], "gain_ft": total["gain_ft"], "loss_ft": total["loss_ft"],
            "hours": hours, "below_aet_hours": hours * 0.8,
            "long": None if long is None else {
                "day": long["day"], "miles": long["miles"], "gain_ft": long["gain_ft"], "loss_ft": long["loss_ft"],
                "minutes": long["minutes"], "effort_mi": effort_miles(long["miles"], long["gain_ft"]),
                "longest_descent_ft": long["longest_descent_ft"],
                "second": None if second is None else {
                    "day": second["day"], "miles": second["miles"], "gain_ft": second["gain_ft"],
                    "loss_ft": second["loss_ft"], "minutes": second["minutes"],
                    "effort_mi": effort_miles(second["miles"], second["gain_ft"])}},
            "limited_by": limited_by if scale < 1 else [], "notes": list(dict.fromkeys(notes)),
            "workout": next((s["kind"] for s in sessions if s["kind"] in WORKOUTS), None),
            "hill_session": pick_hill(c, proposal[i]["hill_session"] if proposal is not None
                                      and proposal[i].get("hill_session") in HILL_SESSIONS
                                      else default_hill_session(rtype, focus, builds,
                                                                any(k in ("taper", "race") for k in kinds[i + 1:i + 7])),
                                      focus, builds),
            "night": night,
            **({"coach_note": str(proposal[i].get("note") or "")[:240]} if proposal is not None else {}),
        })
    common = {"objective": obj.json(), "built_on": today.isoformat(), "constraints": asdict(c),
              "back_to_back": b2b, "race_type": rtype, "goal": obj.goal, "plan_type": plan_type_text(obj),
              "baseline": base, "rates": r, "pace": p, "demand": dem, "peak": peak, "weeks": weeks}
    if not event:
        # No event to be ready for. A yearly target is reachable when the weeks planned cover what's left of it.
        reachable = True
        if obj.goal == "target":
            done = year_done(acts, today)
            planned = {k: sum(w[k] for w in weeks) for k in DIMS}
            totals = {"miles": obj.distance_mi, "gain_ft": obj.gain_ft, "loss_ft": obj.loss_ft}
            reachable = all(done[k] + planned[k] >= totals[k] * 0.97 for k in DIMS if totals[k])
            common["target"] = {"done": done, "planned": planned, "totals": totals}
        return common | {"projected": None, "reachable": reachable}
    # Projected readiness, read at the start of the taper (or the race week): the last 4 weeks
    # before it and the biggest long day in the 8 weeks before it.
    cut = next(i for i, k in enumerate(kinds) if k in ("taper", "race"))
    before = weeks[max(0, cut - 4):cut] or weeks[:1]
    window = weeks[max(0, cut - 8):cut] or weeks[:1]
    longs = [w["long"] for w in window if w["long"]]
    current = capacity(acts, descent_now, today, pairs)
    overlap = (obj.day - today).days <= CAPACITY_DAYS  # today's biggest days still count on the day

    def best(values: list[float], key: str) -> float:
        return max(values + ([current[key]] if overlap else []), default=0.0)

    def biggest(l: dict, key: str) -> float:
        two = l.get("second")
        return max(l[key], pair_size(l[key], two[key]) if two and pairs else 0.0)

    proj_cap = {
        "big_day": best([biggest(l, "effort_mi") for l in longs], "big_day"),
        "time_on_feet": best([biggest(l, "minutes") / 60 for l in longs], "time_on_feet"),
        "climbing_volume": sum(w["gain_ft"] for w in before) / len(before),
        "descent_durability": best([l["longest_descent_ft"] or 0.0 for l in longs], "descent_durability"),
        "descent_volume": descent_fitness_at.get(cut, descent_now) * 7,
        "altitude": current["altitude"],
    }
    projected = evaluate(dem, proj_cap, pairs)
    return common | {"projected": {"capacity": proj_cap, **projected}, "reachable": projected["verdict"] != "gap"}


def _proposed_kinds(proposal: list[dict], n: int, week0: date, event: bool) -> list[str]:
    """The week kinds of a coach's plan, after checking it lines up with the plan's weeks. For an
    event the last week is always the race week; a block has no taper or race week."""
    if len(proposal) != n:
        raise PlanError(f"The coach's plan has {len(proposal)} weeks; the plan has {n}.")
    kinds = []
    for i, pw in enumerate(proposal):
        if pw.get("start") != (week0 + timedelta(weeks=i)).isoformat():
            raise PlanError("The coach's weeks don't line up with the plan's.")
        if not all(isinstance(pw.get(k), (int, float)) and 0 <= pw[k] < 1e6 for k in DIMS):
            raise PlanError("The coach's plan has a week without usable numbers.")
        f = pw.get("focus")
        kinds.append("race" if event and i == n - 1 else "recovery" if f == "recovery"
                     else "taper" if f == "taper" and event and i >= n - 4 else "build")
    return kinds


def options(obj: Objective, acts: list[dict], history: list[LedgerDay], c: Constraints, today: date,
            plan: dict) -> dict:
    """When the objective is out of reach: a smaller version on the same date, and the earliest
    later date with no gap. Neither loosens a limit."""
    if plan["reachable"] or not is_event(obj):
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


MAKEUP = 1.10  # a session ahead grows at most 10% past its planned size to make up what was missed
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


def _own_kind(kind: str, week: dict) -> str:
    """The session an own run counts as. A tempo or intervals run is the week's speed session (it
    keeps its steps when the athlete moves or changes it), or a plain one of its kind."""
    if kind in ("tempo", "intervals"):
        return week.get("workout") if week.get("workout") in WORKOUTS else "tempo" if kind == "tempo" else "track"
    return ("long" if kind in ("long", "race") else "hills" if kind == "hills" else "rest" if kind == "rest"
            else "easy")


def upcoming_sessions(plan: dict, acts: list[dict], today: date, days: int = 7,
                      own: dict[str, dict] | None = None) -> list[dict]:
    """Dated sessions for the next `days` days, cut fresh from each week's targets less what is
    already done that week. Only these days get sessions: later weeks stay as targets, because a
    list of fixed sessions weeks out is wrong by the second week.

    `own` holds the athlete's own scheduled runs by ISO day. They count toward the week the same
    as what's done: the plan spreads only what's left over the other days, and an own run on a
    day takes that day (marked `own`).

    A missed run isn't piled onto the days left: each session ahead stays within MAKEUP of the
    same kind of session in the week's full plan, and the week comes in under target instead."""
    own = own or {}
    c = Constraints.from_json(plan["constraints"])
    obj = plan["objective"]
    obj_day = date.fromisoformat(obj["day"])
    event = is_event(obj)
    pace_min = plan["pace"]["min_per_effort_mi"]
    b2b = bool(plan.get("back_to_back"))
    road_long = road_long_mi(obj["distance_mi"]) if plan.get("race_type") == "road" else None
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
            race = event and _in_week(obj_day, start)
            cw = _around_own(_before(c, obj_day) if race else c, mine, w)
            # The week as it would run from Monday around your own runs, with nothing missed: the
            # sizes a session ahead may grow to, by kind. An own speed or hill run is the week's
            # speed session or hill day, so the plan adds no other.
            kinds = {r["kind"] for r in mine.values()}
            speed = None if kinds & {"tempo", "intervals"} else w.get("workout")
            hill_day = "hills" not in kinds
            full, _ = sessions_for_week({d: max(0.0, w[d] - sum(r[d] for r in mine.values())) for d in DIMS},
                                        start, start, cw, pace_min, b2b and not race, speed, road_long, hill_day)
            most: dict[str, dict[str, float]] = {}
            for s in full:
                m = most.setdefault(s["kind"], dict.fromkeys(DIMS, 0.0))
                for d in DIMS:
                    m[d] = max(m[d], s[d])
            ss, _ = sessions_for_week(left, start, max(first, start), cw, pace_min, b2b and not race, speed,
                                      road_long, hill_day)
            for s in ss:
                m = most.get(s["kind"])
                if m is not None and s["day"] not in mine:
                    scale = min([1.0] + [m[d] * MAKEUP / s[d] for d in DIMS if s[d] > 1e-6])
                    for d in (*DIMS, "minutes"):
                        s[d] *= scale
            for s in ss:
                r = mine.get(s["day"])
                if r is not None:
                    s.update(kind=_own_kind(r["kind"], w), miles=r["miles"], gain_ft=r["gain_ft"], loss_ft=r["loss_ft"],
                             minutes=r.get("minutes") or effort_miles(r["miles"], r["gain_ft"]) * pace_min, own=True)
                elif s["kind"] != "rest" and s["miles"] < MIN_SESSION_MI and s["gain_ft"] < MIN_SESSION_FT:
                    # What's done and your own runs all but cover the week: a scrap of a run isn't worth it.
                    s.update(kind="rest", miles=0.0, gain_ft=0.0, loss_ft=0.0, minutes=0.0)
            for s in ss:
                if s["kind"] == "long2" and race:
                    s["kind"] = "easy"
                if s["kind"] == "long" and not s.get("own"):
                    if race:
                        s["kind"] = "easy"
                    elif w.get("long") and w["long"].get("longest_descent_ft"):
                        s["longest_descent_ft"] = min(s["loss_ft"], w["long"]["longest_descent_ft"])
            type_week(ss, w, c)
            weeks[w["start"]] = {s["day"]: s for s in ss}
        s = dict(weeks[w["start"]][day.isoformat()])
        if day == obj_day and event:
            s = {"day": day.isoformat(), "kind": "objective", "miles": obj["distance_mi"],
                 "gain_ft": obj["gain_ft"], "loss_ft": obj["loss_ft"],
                 "minutes": plan["demand"]["time"]["mid_h"] * 60}
        elif day < first:
            s = {"day": day.isoformat(), "kind": "done", "miles": 0.0, "gain_ft": 0.0, "loss_ft": 0.0, "minutes": 0.0}
        extra = {"race": plan.get("race_type") or "mountain"}
        if s["kind"] in ("long", "long2") and w.get("night"):
            extra["night"] = True
        out.append(s | {"focus": w["focus"]} | extra)
    return out


def _effort(v: dict) -> float:
    return effort_miles(v["miles"], v["gain_ft"])


def week_sessions(plan: dict, acts: list[dict], today: date, start: date, own: dict[str, dict] | None = None) -> list[dict]:
    """A week's seven days: what was done on its past days (kind "past"), then the plan's
    sessions on the rest, around the athlete's own runs and rest days. A week further out than the
    calendar's dated days is the shape it would take from its targets; it is cut fresh once it's
    a week away."""
    end = start + timedelta(days=6)
    first = max(today, start)
    foot = [a for a in acts if is_foot(a["sport"])]
    out = []
    for k in range(max(0, min(7, (first - start).days))):
        d = (start + timedelta(days=k)).isoformat()
        did = [a for a in foot if a["day"] == d]
        out.append({"day": d, "kind": "past", "miles": sum(a["distance_mi"] for a in did),
                    "gain_ft": sum(a["gain_ft"] for a in did), "loss_ft": sum(a["loss_ft"] for a in did),
                    "minutes": sum(a["moving_time_s"] for a in did) / 60})
    if first <= end:
        out += upcoming_sessions(plan, acts, first, (end - first).days + 1, own)
    return out


def week_effective(plan: dict, acts: list[dict], today: date, start: date, own: dict[str, dict]) -> dict:
    """What a week comes to as things stand: what's done on its days so far, plus the athlete's
    own runs and the plan's sessions on the days left (which never make up more than MAKEUP of a
    missed run)."""
    days = week_sessions(plan, acts, today, start, own)
    if start <= today <= start + timedelta(days=6) and any(a["day"] == today.isoformat() and is_foot(a["sport"])
                                                           for a in acts):
        # Today is done: week_sessions shows it as done with nothing in it, so count what was run.
        did = [a for a in acts if a["day"] == today.isoformat() and is_foot(a["sport"])]
        days.append({"miles": sum(a["distance_mi"] for a in did), "gain_ft": sum(a["gain_ft"] for a in did),
                     "kind": "past"})
    return {k: sum(s.get(k, 0.0) for s in days if s.get("kind") != "objective") for k in ("miles", "gain_ft")}


# Adapting the week after a changed one. A week changes when the athlete edits a day in it (their
# own run or a rest day on the calendar); the current week changes as it's run. When it comes out
# well over its full plan in miles or in vert, the next week is eased by half the overshoot; when
# its effort comes out under,
# the next week builds at most BUILD_RATE over what it came to, the plan's own build rule, so it
# doesn't jump to make up what was missed. A week is never made bigger.
ADAPT_OVER, ADAPT_UNDER = 1.10, 0.90
ADAPT_MIN = 0.70
ADAPT_DIMS = ("miles", "gain_ft", "loss_ft", "hours", "below_aet_hours")


def _scale_week(w: dict, f: float) -> None:
    for k in ADAPT_DIMS:
        w[k] = w[k] * f
    if w.get("long"):
        long = dict(w["long"])
        for k in ("miles", "gain_ft", "loss_ft", "minutes", "effort_mi"):
            long[k] = long[k] * f
        if long.get("second"):
            long["second"] = {k: v * f if isinstance(v, (int, float)) else v for k, v in long["second"].items()}
        w["long"] = long


def adapt(plan: dict, acts: list[dict], today: date, own: dict[str, dict] | None = None) -> dict:
    """The plan with the week after each changed week adapted (see ADAPT_OVER). Returns a copy;
    each adapted week carries `adapted` with the factor, the week it follows and why."""
    own = own or {}
    out = plan | {"weeks": [dict(w) for w in plan["weeks"]]}
    weeks = out["weeks"]
    monday = today - timedelta(days=today.weekday())
    edited = {(date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())).isoformat() for d in own}
    for i, w in enumerate(weeks[:-1]):
        start = date.fromisoformat(w["start"])
        nxt = weeks[i + 1]
        if start < monday - timedelta(weeks=1) or (w["start"] not in edited and start != monday):
            continue
        if nxt["focus"] == "race" or date.fromisoformat(nxt["start"]) < monday:
            continue
        planned = week_effective(out, [], start, start, {})
        full = _effort(planned)
        if full < 1:
            continue
        now = week_effective(out, acts, today, start, own)
        came = _effort(now)
        r = came / full
        # Over in either miles or vert: a big climbing day in a light week is over even when its miles aren't.
        over = max([now[k] / planned[k] for k, least in (("miles", 1.0), ("gain_ft", 200.0)) if planned[k] > least],
                   default=r)
        nstart = date.fromisoformat(nxt["start"])
        if over > ADAPT_OVER:
            r = over
            f, why = max(ADAPT_MIN, 1 - (r - 1) * 0.5), "over"
        elif r < ADAPT_UNDER and (nfull := _effort(week_effective(out, [], nstart, nstart, {}))) > 1:
            f, why = max(ADAPT_MIN, min(1.0, came * BUILD_RATE / nfull)), "under"
        else:
            continue
        if f >= 0.98:
            continue
        _scale_week(nxt, f)
        label = f"{start:%b} {start.day}"
        text = (f"{round((1 - f) * 100)}% lighter: the week of {label} comes out at {round(r * 100)}% of its plan"
                + (" in miles or vert" if why == "over" else "")
                + (", so this one eases off to absorb it" if why == "over" else
                   f", so this one builds at most {round((BUILD_RATE - 1) * 100)}% on it instead of jumping to make it up"))
        nxt["adapted"] = {"factor": round(f, 3), "from": w["start"], "reason": why, "share": round(r, 3), "text": text}
        nxt["notes"] = [text, *nxt.get("notes", [])]
    return out


SESSION_TITLE = {"long": "Long day", "long2": "Back-to-back day", "hills": "Hills", **WORKOUTS, "easy": "Easy aerobic", "rest": "Rest",
                 "objective": "Objective", "done": "Done for today"}


def _reps(s: dict, steps: tuple) -> int | str:
    """How many reps: fewer in a taper or recovery week, more at the peak."""
    return {"taper": steps[0], "recovery": steps[0], "peak": steps[2]}.get(s.get("focus"), steps[1])


def hr_zones(aet: float, ant: float | None) -> dict[str, str]:
    """Heart-rate targets from the athlete's own thresholds: recovery well under AeT, zone 2 just
    under it, steady between AeT and tempo, tempo and threshold just under AnT, hard above it.
    Without an AnT, the hard zones are described by feel."""
    z = {"easy": f"under {aet:.0f} bpm", "recovery": f"zone 1, under {aet - 20:.0f} bpm",
         "aerobic": f"zone 2, {aet - 10:.0f} to {aet:.0f} bpm"}
    if ant and ant > aet + 6:
        lo = max(aet + 1, ant - 12)
        return z | {"steady": f"{aet:.0f} to {lo:.0f} bpm", "tempo": f"zone 3, {lo:.0f} to {ant - 5:.0f} bpm",
                    "threshold": f"{ant - 5:.0f} to {ant:.0f} bpm, right at your AnT",
                    "hard": f"{ant:.0f} bpm and up, over your AnT"}
    return z | {"steady": f"just over {aet:.0f} bpm", "tempo": "comfortably hard, a pace you could hold for an hour",
                "threshold": "hard but even, a pace you could hold for 30 to 40 min",
                "hard": "hard, about your 5k effort"}


STRENGTH = ("Then 25 min of strength: 3 rounds of 10 step-ups a leg, 10 split squats a leg, 10 single-leg "
            "deadlifts a leg, 15 slow step-downs off a box a leg (for the descents), 20 calf raises and a 45 s plank.")
EASY_TITLE = {"easy": "Easy aerobic", "recovery": "Recovery run", "strides": "Easy run with strides"}


def _workout_steps(s: dict, z: dict) -> list[str]:
    """How to run a speed session, warm-up to cool-down."""
    warm, cool = f"Warm up 15 min easy, {z['easy']}.", "Cool down 10 min easy."
    kind = s["kind"]
    if kind == "track":
        return [warm + " Add 4 × 20 s strides.",
                f"{_reps(s, (4, 6, 8))} × 800 m at {z['hard']}, about 5k effort, with 400 m of easy jogging between.",
                cool, "A track or any flat path."]
    if kind == "threshold":
        return [warm, f"{_reps(s, (3, 4, 5))} × 6 min at {z['threshold']}, 2 min easy jogging between.", cool]
    if kind == "tempo":
        return [warm, f"{_reps(s, ('2 × 8', '2 × 15', '3 × 12'))} min at {z['tempo']}, 3 min easy between.", cool]
    if kind == "race_pace":
        return [warm, f"{_reps(s, (3, 5, 8))} mi steady at your goal race pace, on terrain like the race's, taking a "
                      "gel or drink when you will in the race.", cool]
    if kind == "fartlek":
        return [warm, f"{_reps(s, (4, 5, 6))} rounds of 2 min at {z['tempo']}, 1 min at {z['hard']} and 2 min easy, "
                      "on rolling trail: let the hills set where the hard bits fall.", cool]
    if kind == "progression":
        return [f"Start easy, {z['easy']}, for the first half.",
                f"Then step it up every 10 min: through {z['steady']}, finishing the last 15 to 20 min at {z['tempo']}.",
                "Jog 5 min easy to finish."]
    return [warm, f"{_reps(s, (5, 8, 10))} × 2 min hard uphill on a 6 to 10% grade at {z['hard']}, walking or "
                  "jogging down to recover.", cool]


def _hill_steps(s: dict, z: dict, kind: str) -> list[str]:
    """How to run the week's hill day."""
    if kind == "power_hike":
        return [f"Warm up 15 min easy on gentle terrain, {z['easy']}.",
                f"{_reps(s, (3, 5, 6))} × 6 min power-hiking a steep grade (15% or more), hands on knees or poles, "
                f"at {z['steady']}: short quick steps, upright, arms working.",
                "Walk or jog down between. Run the rest of the day easy in zone 2."]
    if kind == "downhill":
        return [f"Climb to the top of a smooth, moderately steep trail in {z['aerobic']}.",
                f"{_reps(s, (3, 5, 6))} × 3 to 5 min downhill: quick light steps under your hips, eyes ahead, "
                f"controlled rather than fast; hike or jog back up in {z['aerobic']}.",
                "Expect your quads sore for a day or two at first."]
    if kind == "muscular_endurance":
        return [f"Warm up 15 min easy, {z['easy']}.",
                f"With a pack of 10 to 20% of your body weight (start light), hike your steepest climb for "
                f"{_reps(s, (20, 40, 60))} min at {z['steady']}. No hill: step-ups on a box or bench.",
                "Your legs should burn but your breathing stay easy. Dump water at the top to spare your knees "
                "on the way down."]
    if kind == "hill_sprints":
        return [f"Warm up 15 min easy, {z['easy']}.",
                f"{_reps(s, (6, 8, 10))} × 10 to 15 s near all-out up a steep hill, walking back down and resting "
                "about 2 min between: power and form, not fitness.",
                f"Run the rest easy, {z['aerobic']}."]
    return ["Take the longest climb you can reach.",
            f"Climb steady in {z['aerobic']}: run what's runnable and power-hike anything steep.",
            "Descend relaxed, with quick feet."]


# What each session does for the athlete, one or two plain sentences, no numbers.
BENEFITS = {
    "rest": "Your body absorbs the training while you rest: fitness is built in recovery, not in the session.",
    "long": "Builds the endurance and time on feet your objective needs, trains your legs for hours of climbing "
            "and descending, and teaches your gut to take food on the move.",
    "long_road": "Builds aerobic endurance and the fuel stores and leg resilience for the late miles of the race.",
    "long2": "Trains you to move well on tired legs, the way the second half of a long race feels, without the "
             "risk of one huge day.",
    "track": "Raises your aerobic ceiling and running economy, so every pace below it, race pace included, feels easier.",
    "threshold": "Lifts your anaerobic threshold, the hardest effort you can hold for about an hour, so you can "
                 "run and climb harder before you tire.",
    "tempo": "Teaches you to hold a strong, steady effort for a long time and pushes your threshold up.",
    "race_pace": "Rehearses your goal pace, fueling and terrain so race day feels familiar, and locks in the effort.",
    "fartlek": "Teaches you to change gears on rolling ground and recover while still moving, with less strain "
               "than structured intervals.",
    "progression": "Trains you to finish strong on tired legs and builds steady-state fitness at a low recovery cost.",
    "hill_repeats": "Builds climbing strength and power and raises your aerobic ceiling, with less pounding than flat speed work.",
    "vert_climb": "Builds climbing endurance and your aerobic base on the uphill, and gets your legs used to long "
                  "sustained grades.",
    "power_hike": "Makes your hiking on steep ground faster and more efficient: most mountain races walk the steep "
                  "climbs, and a strong hike saves minutes and energy.",
    "downhill": "Builds the quads for long descents, the eccentric strength that keeps them working late in a "
                "race when the downhills usually wreck them.",
    "muscular_endurance": "Builds leg strength endurance for long climbs without the pounding of running, for the "
                          "late hours when your legs, not your breathing, slow you.",
    "hill_sprints": "Builds power, stride strength and good form with almost no fatigue, and toughens tendons "
                    "against injury.",
    "recovery": "Gets blood moving through tired legs after the long day and adds easy aerobic time without adding fatigue.",
    "strides": "Keeps your legs quick and your form efficient without adding fatigue.",
    "easy": "Builds the aerobic base that every long day rests on, more capillaries and better fat burning, "
            "with little fatigue.",
}
STRENGTH_BENEFIT = "The strength work protects your knees and hips on the descents and keeps your stride strong late."
NIGHT_BENEFIT = "The dark section practices headlamp running and staying alert when you'd normally sleep."


def session_benefit(s: dict) -> str | None:
    """What the session does for the athlete."""
    kind = s["kind"]
    kind_of = s.get("type") or kind
    if kind in ("done", "objective"):
        return None
    if kind == "hills":
        key = kind_of if kind_of in HILL_SESSIONS else "vert_climb"
    elif kind == "long" and s.get("race") == "road":
        key = "long_road"
    elif kind_of in ("recovery", "strides"):
        key = kind_of
    else:
        key = kind if kind in BENEFITS else "easy"
    out = [BENEFITS[key]]
    if s.get("night") and kind in ("long", "long2"):
        out.append(NIGHT_BENEFIT)
    if s.get("strength"):
        out.append(STRENGTH_BENEFIT)
    return " ".join(out)


def session_text(s: dict, aet: float, objective_name: str | None = None, ant: float | None = None) -> dict:
    """The words for a session: a title, the steps to run it, how (the steps as one line), and the
    full detail with its numbers. Every number is the engine's or the athlete's own heart rate."""
    kind = s["kind"]
    kind_of = s.get("type") or kind
    z = hr_zones(aet, ant)
    if kind == "done":
        return {"title": "Done for today", "how": "Today's training is in. Recover.",
                "detail": "Today's training is in. Recover.", "steps": [], "benefit": None}
    if kind == "rest":
        steps = [f"Rest, or 30 to 45 min of easy cross-training (bike, swim or a walk) in {z['recovery']}.",
                 "10 min of mobility: hips, ankles and calves."]
        how = " ".join(steps)
        return {"title": "Rest", "how": how, "detail": how, "steps": steps, "benefit": BENEFITS["rest"]}
    vol = f"{s['miles']:.1f} mi, {round(s['gain_ft'], -1):,.0f} ft up, {round(s['loss_ft'], -1):,.0f} ft down"
    if kind == "objective":
        return {"title": objective_name or "Objective", "how": "Go well.", "detail": f"{vol}. Go well.", "steps": [],
                "benefit": None}
    mins = f"about {round(s['minutes'] / 5) * 5:.0f} min"
    race = s.get("race", "mountain")
    if kind == "long" and race == "road":
        title = "Long run"
        steps = [f"Easy and conversational, {z['aerobic']}."]
        if s.get("focus") == "peak" and s["miles"] >= 10:
            steps.append("Run the last 2 to 3 mi at goal race pace.")
    elif kind == "long":
        title = "Long day"
        steps = [f"Mostly {z['aerobic']}; power-hike the steep climbs so you stay under {aet:.0f} bpm.",
                 "Eat and drink from the first hour, the way you will on race day."]
        if s.get("longest_descent_ft"):
            steps.insert(1, f"Include one continuous descent of about {round(s['longest_descent_ft'], -2):,.0f} ft.")
    elif kind == "long2":
        title = "Back-to-back day"
        steps = [f"The second of two long days, on tired legs: {z['aerobic']}, hiking the climbs.",
                 "Practice moving well downhill and eating while tired."]
    elif kind in WORKOUTS:
        title, steps = WORKOUTS[kind], _workout_steps(s, z)
    elif kind == "hills":
        title, steps = HILL_SESSIONS.get(kind_of, "Hills"), _hill_steps(s, z, kind_of)
    elif kind_of == "recovery":
        title = EASY_TITLE["recovery"]
        steps = [f"Short and slow in {z['recovery']}, on the gentlest ground you have.",
                 "Heavy legs: swap it for an easy bike or a walk."]
    elif kind_of == "strides":
        title = EASY_TITLE["strides"]
        steps = [f"Easy in {z['aerobic']}.",
                 "In the last 10 min, 6 × 20 s strides: quick and relaxed at about mile pace, walking back between."]
    else:
        title = EASY_TITLE["easy"]
        steps = [f"Easy and conversational in {z['aerobic']}; hike any climb that pushes you over {aet:.0f} bpm."]
    if kind in ("long", "long2") and race in ("hundred", "multiday"):
        steps.append("Power-hike every climb at race effort, and eat about 250 kcal an hour with your race food.")
        if s.get("night"):
            steps.append("Run part of it in the dark with your race headlamp."
                         + (" Practice a 20 min sleep stop." if race == "multiday" else ""))
    if s.get("strength"):
        title += " + strength"
        steps.append(STRENGTH)
    how = " ".join(steps)
    return {"title": title, "how": how, "detail": f"{vol}, {mins}. {how}", "steps": steps,
            "benefit": session_benefit(s)}


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
    "rolling": "A new week: your ongoing plan moved on with your latest training",
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
