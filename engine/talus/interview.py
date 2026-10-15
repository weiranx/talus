"""Planning with the coach: a short conversation that fills the same inputs as the plan form.

The coach asks eight questions, most answered with a tap: the objective (and says what kind of
plan it makes), the goal, training days and time, terrain, the workouts to include, the body, and
last, a summary of what it will build and anything else the athlete wants, before it drafts. Its questions and confirmations are
written here, so they cost nothing; only free text (the objective in your own words, or a change
you ask for after the draft) goes to the cheaper model, and only to be read into fields. The
engine drafts every number. Without a model, simple rules read the common phrasings.
Distances are miles, vert is feet.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import plan as planner
from .plan import DAY_NAMES, GOAL_KINDS, HILL_SESSIONS, MAX_WEEKDAY_MIN, MAX_WEEKEND_MIN, WORKOUTS

DAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
KM_PER_MI = 1.609344
STEPS = ("objective", "goal", "days", "time", "terrain", "workouts", "body", "confirm")

GOALS = ("Finish strong", "A time goal", "Just get round")
TERRAIN = ("Big climbs nearby", "Rolling hills", "Mostly flat or treadmill")
BODY = ("Nothing to note", "A small niggle", "An injury I'm managing")
FEEL = ("Fresh", "Normal", "Tired lately")
WEEKDAY_MIN = {"45 min": 45, "1 h": 60, "1 h 30": 90, "2 h": 120, "3 h": 180, "4 h": 240}
WEEKEND_MIN = {"2 h": 120, "3 h": 180, "4 h": 240, "6 h": 360, "8 h": 480, "10 h": 600, "12 h": 720}
LESS, MORE = 0.85, 1.10  # "less" or "more" for a single week; the engine still checks the limits
RECOVERY_WEEK = 0.70  # "make week 3 a recovery week"

# The workouts question: speed sessions and hill sessions rotate through the build, extras go on
# easy days. Chip labels to engine keys.
SPEED_CHIPS = {"Track intervals": "track", "Threshold": "threshold", "Tempo": "tempo", "Fartlek": "fartlek",
               "Progression runs": "progression", "Race pace": "race_pace", "Hill repeats": "hill_repeats"}
HILL_CHIPS = {"Long climbs": "vert_climb", "Power hiking": "power_hike", "Downhill repeats": "downhill",
              "Weighted-pack hiking": "muscular_endurance", "Hill sprints": "hill_sprints"}
EXTRA_CHIPS = {"Strides": "strides", "Strength": "strength", "Back-to-back long days": "back_to_back"}
WORKOUT_GROUPS = (("speed", SPEED_CHIPS, "Speed"), ("hills", HILL_CHIPS, "Hill day"), ("extras", EXTRA_CHIPS, "Extras"))
DRAFT_IT = "Draft my plan"
# After a draft, quick changes that ask the setup question again and redraft from the answer.
CHANGE_TOPICS = {"Workouts": "workouts", "Training days": "days", "Time limits": "time"}
CHANGE_GROUP = {"key": "change_topic", "options": list(CHANGE_TOPICS), "label": "Quick changes", "value": None}

# Goals with no race day, as chips on the first question.
GOAL_CHIPS = {"Build base": "base", "Maintain fitness": "maintain", "Come back from a break": "comeback",
              "Get faster": "faster", "Yearly target": "target", "Ongoing plan": "rolling"}

QUESTIONS = {
    "objective": {"text": "What are you training for? For a race or a mountain objective, tell me the name, the date, "
                          "the distance and the vert, like \"Baldy 50k on Jan 2, 31 mi, 8,000 ft\", or upload the "
                          "route's GPX. No race day? Pick a goal below.",
                  "groups": [{"key": "goal_kind", "options": list(GOAL_CHIPS), "label": "No race day"}]},
    "goal": {"text": "What's the goal for the day?", "groups": [{"key": "goal", "options": list(GOALS)}]},
    "days": {"text": "Which days can you train, and which one is your long day?",
             "groups": [{"key": "days", "options": list(DAY_SHORT), "multi": True},
                        {"key": "long_day", "options": list(DAY_SHORT), "label": "Long day"}]},
    "time": {"text": "How long can you go on a weekday, and on a weekend day?",
             "groups": [{"key": "weekday", "options": list(WEEKDAY_MIN), "label": "Weekdays"},
                        {"key": "weekend", "options": list(WEEKEND_MIN), "label": "Weekends"}]},
    "terrain": {"text": "What vert can you get to from home?", "groups": [{"key": "terrain", "options": list(TERRAIN)}]},
    "body": {"text": "Anything hurting, and how has training felt lately?",
             "groups": [{"key": "body", "options": list(BODY), "label": "Body"},
                        {"key": "feel", "options": list(FEEL), "label": "Lately"}]},
    "workouts": {"text": "Which workouts do you want in your plan?",
                 "groups": [{"key": k, "options": list(chips), "label": label, "multi": True}
                            for k, chips, label in WORKOUT_GROUPS]},
    "confirm": {"text": "Anything else you want from this plan?", "groups": [{"key": "draft_it", "options": [DRAFT_IT]}]},
}


def coach(text: str, step: str | None = None, groups: list | None = None, actions: list | None = None) -> dict:
    return {"role": "coach", "text": text, "step": step, "groups": groups or [], "actions": actions or []}


def ask(step: str, defaults: dict | None = None) -> dict:
    q = QUESTIONS[step]
    return coach(q["text"], step, [g | {"value": (defaults or {}).get(g["key"])} for g in q["groups"]])


def start(c: dict, current: dict | None = None) -> dict:
    """A fresh conversation. Day answers start from the saved training days; an objective already
    set can be kept with a tap."""
    first = ask("objective")
    if current:
        line = objective_line(current) if planner.is_event(current) else block_line(current)
        first = coach(f"You're set up for {line}. Keep it, or tell me what else you're training for. You can "
                      "also upload the route's GPX, or pick a goal with no race day.", "objective",
                      [{"key": "keep", "options": ["Keep it"], "value": None}, QUESTIONS["objective"]["groups"][0]])
    return {"messages": [coach("Let's build your plan. A few quick questions, and I'll draft it from your "
                               "last four weeks of training."), first],
            "step": "objective", "mode": "setup", "answers": {}, "objective": {}, "defaults": days_defaults(c)}


def change(name: str, c: dict) -> dict:
    """A conversation about changing the plan you have."""
    return {"messages": [coach(f"What would you like to change in your plan for {name}? Tap a quick change, or tell "
                               "me. " + CHANGE_EXAMPLES, "draft", [CHANGE_GROUP])],
            "step": "draft", "mode": "change", "answers": {}, "objective": {}, "defaults": days_defaults(c)}


CHANGE_EXAMPLES = ("For example: \"move my long day to Sunday\", \"I can't run Tuesdays\", \"add hill sprints\", "
                   "\"make week 3 a recovery week\" or \"skip this Saturday\".")


def said(step: str, answers: dict) -> str:
    """Chip answers as the athlete's line in the conversation."""
    if step == "workouts" and any(k in answers for k, _, _ in WORKOUT_GROUPS):
        picked = [v for k, _, _ in WORKOUT_GROUPS for v in (answers.get(k) or [])]
        return ", ".join(picked) if picked else "None of these"
    if answers.get("change_topic"):
        return str(answers["change_topic"])
    parts = []
    for g in QUESTIONS[step]["groups"] if step in QUESTIONS else []:
        v = answers.get(g["key"])
        if not v:
            continue
        v = ", ".join(v) if isinstance(v, list) else v
        parts.append(f"{g['label'].lower()} {v}" if g.get("label") and g["key"] == "long_day" else
                     f"{g['label']}: {v}" if g.get("label") else v)
    if answers.get("keep"):
        parts.append("Keep it")
    if answers.get("confirm"):
        parts.append(str(answers["confirm"]))
    return " · ".join(parts)


def match(option_text: str, options: list[str]) -> str | None:
    """The chip a typed answer names, if any."""
    t = option_text.strip().lower()
    for o in options:
        if t == o.lower() or (len(t) >= 4 and (t in o.lower() or o.lower() in t)):
            return o
    return None


def guess(step: str, text: str) -> str:
    """The closest chip for typed words that don't name one."""
    t = text.lower()
    if step == "goal":
        return GOALS[1] if re.search(r"\d|\btime\b|\bsub\b|\bpr\b|fast|cutoff|cut-off", t) else GOALS[2] if re.search(r"survive|round|around|just finish", t) else GOALS[0]
    if step == "terrain":
        return TERRAIN[2] if re.search(r"flat|treadmill|city|no hills|stairs", t) else TERRAIN[0] if re.search(r"mountain|big|steep|trail", t) else TERRAIN[1]
    return BODY[0] if re.search(r"\b(nothing|no|none|fine|good|healthy)\b", t) and not re.search(r"sore|pain|hurt|injur|niggle|tight", t) else BODY[2] if re.search(r"injur|rehab|physio|pt\b", t) else BODY[1]


def chip_answers(step: str, answers: dict, text: str, defaults: dict) -> tuple[dict | None, str | None]:
    """A step's answers from chips or typed words, or why they can't be used yet."""
    a = {k: v for k, v in (answers or {}).items() if v not in (None, "", [])}
    if step in ("goal", "terrain", "body"):
        key = QUESTIONS[step]["groups"][0]["key"]
        opts = QUESTIONS[step]["groups"][0]["options"]
        if text and key not in a:
            a[key] = match(text, opts) or guess(step, text)
        if text:
            a[f"{step}_note"] = text[:200]
        if step == "body" and text and "feel" not in a:
            a["feel"] = match(text, list(FEEL)) or ("Tired lately" if re.search(r"\btired|fatigu|exhaust|worn", text, re.IGNORECASE) else None)
        a = {k: v for k, v in a.items() if v is not None}
        if a.get(key) not in opts:
            return None, "Tap one of the answers, or type a few words."
        if step == "body" and a.get("feel") not in (None, *FEEL):
            a.pop("feel")
        return a, None
    if step == "days":
        days = a.get("days")
        long_day = a.get("long_day")
        if text:
            t = text.lower()
            if m := re.search(r"long[^.]*?" + DAY_RE, t):
                long_day = DAY_SHORT[DAY_WORDS[m[1]]]
            named = [DAY_SHORT[DAY_WORDS[m[1]]] for m in re.finditer(DAY_RE, t)]
            if "weekend" in t:
                named += ["Sat", "Sun"]
            if "weekday" in t:
                named += list(DAY_SHORT[:5])
            dropped = {DAY_SHORT[DAY_WORDS[m[1]]] for m in re.finditer(r"\b(?:except|not|no|but)\s+" + DAY_RE, t)}
            named = [d for d in named if d not in dropped or d == long_day]
            if named:
                days = named
        days = [d for d in DAY_SHORT if d in (days or defaults.get("days") or [])]
        long_day = long_day or defaults.get("long_day")
        if not days:
            return None, "Pick at least one day you can train."
        if long_day not in DAY_SHORT:
            return None, "Which day is your long day?"
        if long_day not in days:
            days = [d for d in DAY_SHORT if d in days + [long_day]]
        return {"days": days, "long_day": long_day}, None
    if step == "workouts":
        if text and not any(k in (answers or {}) for k, _, _ in WORKOUT_GROUPS):
            # Typed instead of tapped: the workouts the words name, on top of the plan type's picks.
            t = text.lower()
            for k, chips, _ in WORKOUT_GROUPS:
                named = [label for label in chips if label.lower().rstrip("s") in t or chips[label] in t.replace(" ", "_")]
                a[k] = list(dict.fromkeys([*(defaults.get(k) or []), *named]))
            if re.search(r"\b(?:no|none|nothing|skip)\b", t) and not any(a.get(k) for k, _, _ in WORKOUT_GROUPS[:2]):
                a = {k: [] for k, _, _ in WORKOUT_GROUPS}
        out = {k: [o for o in chips if o in (a.get(k) if k in a else (answers or {}).get(k) or [])]
               for k, chips, _ in WORKOUT_GROUPS}
        if text:
            out["workouts_note"] = text[:200]
        return out, None
    if step == "confirm":
        return ({"wants": text[:600]} if text else {}), None
    if step == "time":
        wd, we = a.get("weekday") or defaults.get("weekday"), a.get("weekend") or defaults.get("weekend")
        out = {}
        for key, table, value in (("weekday", WEEKDAY_MIN, wd), ("weekend", WEEKEND_MIN, we)):
            if value in table:
                out[key + "_min"] = table[value]
        if text:
            t = text.lower()
            for key in ("weekday", "weekend"):
                if key in t and (v := _minutes(t.split(key, 1)[0][-30:] + " " + t.split(key, 1)[1][:30])):
                    out[key + "_min"] = v
            if "weekday" not in t and "weekend" not in t and (v := _minutes(t)):
                out.setdefault("weekday_min", v)
        if "weekday_min" not in out or "weekend_min" not in out:
            return None, "Pick how long you can go on a weekday and on a weekend day."
        if not 15 <= out["weekday_min"] <= MAX_WEEKDAY_MIN or not 15 <= out["weekend_min"] <= MAX_WEEKEND_MIN:
            return None, (f"Weekdays can be 15 min to {MAX_WEEKDAY_MIN // 60} h and weekend days 15 min to "
                          f"{MAX_WEEKEND_MIN // 60} h.")
        return out, None
    return None, None


def from_intake(r: dict, today: date) -> dict:
    """The model's reading of the objective, checked and in miles and feet."""
    out: dict = {}
    if isinstance(r.get("name"), str) and (name := clean_name(r["name"].strip())):
        out["name"] = name[:80]
    if isinstance(r.get("date"), str):
        try:
            out["day"] = date.fromisoformat(r["date"]).isoformat()
        except ValueError:
            pass
    mi = r.get("distance_mi") or (r.get("distance_km") and r["distance_km"] / KM_PER_MI)
    if isinstance(mi, (int, float)) and 0 < mi <= 300:
        out["distance_mi"] = round(float(mi), 1)
    for k in ("gain", "loss"):
        v = r.get(f"{k}_ft") or (r.get(f"{k}_m") and r[f"{k}_m"] * FT_PER_M)
        if isinstance(v, (int, float)) and 0 < v <= 80000:
            out[f"{k}_ft"] = round(float(v))
    return out


FT_PER_M = 3.28084


def from_edits(edits: list[dict]) -> list[dict]:
    """The model's edits in the shape apply_edits takes; anything malformed is dropped."""
    out = []
    for e in edits or []:
        k = e.get("kind")
        if k in ("long_day", "add_day", "drop_day") and isinstance(e.get("day"), int) and 0 <= e["day"] <= 6:
            out.append({"kind": k, "day": e["day"]})
        elif k in ("weekday_minutes", "weekend_minutes") and isinstance(e.get("minutes"), int):
            out.append({"kind": k, "minutes": e["minutes"]})
        elif k == "workout" and e.get("workout") in (*WORKOUTS, "none"):
            out.append({"kind": "workout", "workout": e["workout"]})
        elif k == "week" and isinstance(e.get("week"), int) and e.get("direction") in ("less", "more", "recovery"):
            out.append({"kind": "week", "week": e["week"], "dims": [d for d in e.get("dims") or [] if d in ("miles", "vert")]
                        or ["miles", "vert"],
                        "factor": {"less": LESS, "more": MORE, "recovery": RECOVERY_WEEK}[e["direction"]]})
        elif k in ("add_workout", "drop_workout") and e.get("workout") in PICKABLE:
            out.append({"kind": k, "workout": e["workout"]})
        elif k == "back_to_back" and isinstance(e.get("on"), bool):
            out.append({"kind": k, "on": e["on"]})
        elif k == "rest_day" and isinstance(e.get("date"), str):
            try:
                out.append({"kind": k, "date": date.fromisoformat(e["date"]).isoformat()})
            except ValueError:
                pass
    return out


PICKABLE = (*WORKOUTS, *HILL_SESSIONS, "strides", "strength")
# Words for each workout in a typed change, most specific first.
WORKOUT_WORDS = (
    ("hill_repeats", r"hill (?:repeats|reps|intervals)|uphill (?:repeats|intervals)"),
    ("hill_sprints", r"hill sprints?"),
    ("race_pace", r"(?:race|goal|marathon) pace"),
    ("track", r"track|800s?\b"),
    ("threshold", r"threshold|cruise"),
    ("tempo", r"tempo"),
    ("fartlek", r"fartlek"),
    ("progression", r"progression"),
    ("power_hike", r"power[- ]?hik\w*"),
    ("downhill", r"downhill|descent repeats"),
    ("muscular_endurance", r"weighted|pack hik\w*|muscular endurance"),
    ("vert_climb", r"long climbs?|steady climbs?"),
    ("strides", r"strides"),
    ("strength", r"strength|gym|weights"),
)
ADD_WORDS = r"\b(?:add|include|also|plus|more|want|with|put in|start)\b"
DROP_WORDS = r"\b(?:no|drop|remove|without|stop|skip|cut|less|fewer|don't|do not|take out|hate)\b"


def rules_workouts(t: str) -> list[dict]:
    """Workouts added or dropped in typed words: "add hill sprints", "no strength", "more power
    hiking". Each mention reads the words just before it in its clause."""
    out, seen = [], set()
    taken: list[tuple[int, int]] = []
    for key, words in WORKOUT_WORDS:
        for m in re.finditer(r"\b(?:" + words + r")", t):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append(m.span())
            before = re.split(r"[.;,]|\band\b|\bbut\b", t[:m.start()])[-1][-40:]
            kind = "drop_workout" if re.search(DROP_WORDS, before) else "add_workout" if re.search(ADD_WORDS, before) else None
            if kind and key not in seen:
                seen.add(key)
                out.append({"kind": kind, "workout": key})
    return out


def rules_rest_days(t: str, today: date) -> tuple[list[dict], str]:
    """One-off days off: "skip this Saturday", "I'm away tomorrow", "rest on Nov 3". Returns the
    edits and the text without those phrases, so a day named once isn't read as dropped every week."""
    out = []
    off = r"\b(?:skip|rest|off|can't|cannot|can not|miss|away|busy|travel\w*)\b"
    if not re.search(off, t):
        return out, t
    for m in list(re.finditer(r"\b(this|next|coming)\s+" + DAY_RE.replace("s?\\b", "\\b") + r"|\btomorrow\b|\btoday\b", t)):
        if m.group(0) in ("tomorrow", "today"):
            d = today + timedelta(days=1 if m.group(0) == "tomorrow" else 0)
        else:
            want = DAY_WORDS[m[2]]
            d = today + timedelta(days=(want - today.weekday()) % 7)
            if m[1] == "next" and d - timedelta(days=d.weekday()) == today - timedelta(days=today.weekday()):
                d += timedelta(weeks=1)
        out.append({"kind": "rest_day", "date": d.isoformat()})
        t = t.replace(m.group(0), " ")
    if not out and (d := parse_date(t, today)) and d >= today:
        out.append({"kind": "rest_day", "date": d.isoformat()})
    return out, t


def days_defaults(c: dict) -> dict:
    return {"days": [DAY_SHORT[d] for d in c.get("days", [])], "long_day": DAY_SHORT[c.get("long_day", 5)],
            "weekday": next((k for k, v in WEEKDAY_MIN.items() if v == c.get("weekday_min")), None),
            "weekend": next((k for k, v in WEEKEND_MIN.items() if v == c.get("weekend_min")), None)}


def workout_defaults(c: dict, ptype: str) -> dict:
    """The workouts chips picked to start with: the athlete's own picks when they have made them,
    otherwise what the plan type usually has."""
    d = planner.default_workouts(ptype)
    speed = c.get("workouts") if c.get("workouts") is not None else (
        [c["workout"]] if c.get("workout") in WORKOUTS else d["workouts"])
    hills = c.get("hill_sessions") if c.get("hill_sessions") is not None else d["hill_sessions"]
    extras = {"strides": c.get("strides", True), "strength": c.get("strength", True),
              "back_to_back": c.get("back_to_back", True) if c.get("workouts") is not None else d["back_to_back"]}
    return {"speed": [k for k, v in SPEED_CHIPS.items() if v in speed],
            "hills": [k for k, v in HILL_CHIPS.items() if v in hills],
            "extras": [k for k, v in EXTRA_CHIPS.items() if extras[v]]}


def ask_workouts(ptype: dict, defaults: dict) -> dict:
    """The workouts question, with the plan type's usual picks already on."""
    text = (f"Which workouts do you want in your plan? I've picked what a {ptype['label'].lower()} usually has; tap to "
            "add or drop any. Speed sessions take turns one a week through the build, hill sessions take turns on the "
            "hill day, and strides and strength go on easy days. With no hill session picked, the hill day is a "
            "steady climb.")
    return coach(text, "workouts", [g | {"value": defaults.get(g["key"])} for g in QUESTIONS["workouts"]["groups"]])


def workout_constraints(a: dict) -> dict:
    """The workouts answers as constraints for the engine."""
    extras = {EXTRA_CHIPS[x] for x in a.get("extras") or [] if x in EXTRA_CHIPS}
    speed = [SPEED_CHIPS[x] for x in a.get("speed") or [] if x in SPEED_CHIPS]
    return {"workouts": speed, "hill_sessions": [HILL_CHIPS[x] for x in a.get("hills") or [] if x in HILL_CHIPS],
            "strides": "strides" in extras, "strength": "strength" in extras, "back_to_back": "back_to_back" in extras,
            "workout": speed[0] if speed else "none"}


def type_line(o: dict) -> str:
    """What kind of plan an objective makes, told as soon as the objective is set."""
    t = planner.plan_type_text(o)
    return f"That makes this a {t['label'].lower()}: {t['text']}."


def _time(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h} h {m:02d}" if h and m else f"{h} h" if h else f"{m} min"


def confirm_text(o: dict, a: dict) -> str:
    """Before drafting: what the coach will build from the answers, and the question of what else
    the athlete wants."""
    t = planner.plan_type_text(o)
    what = objective_line(o | {"loss_ft": o.get("loss_ft") or o["gain_ft"]}) if planner.is_event(o) else block_line(o)
    days = [d for d in DAY_SHORT if d in a.get("days", [])]
    long_day = DAY_NAMES[DAY_SHORT.index(a["long_day"])] if a.get("long_day") in DAY_SHORT else None
    picked = [*(a.get("speed") or []), *(a.get("hills") or []), *(a.get("extras") or [])]
    lines = [f"Before I draft it, here's what I'll build: a {t['label'].lower()} for {what}. It's built around {t['text']}."]
    if a.get("goal"):
        lines.append(f"Goal for the day: {a['goal'].lower()}.")
    if days:
        lines.append(f"Training {', '.join(days)}" + (f", long day on {long_day}" if long_day else "")
                     + (f", up to {_time(a['weekday_min'])} on weekdays and {_time(a['weekend_min'])} on weekend days"
                        if a.get("weekday_min") and a.get("weekend_min") else "") + ".")
    if a.get("terrain"):
        lines.append(f"Vert near home: {a['terrain'].lower()}.")
    lines.append(f"Workouts: {_join_words([p.lower() for p in picked])}." if picked else
                 "Workouts: easy running and a steady climb on the hill day, no speed sessions.")
    if a.get("body"):
        lines.append(f"Body: {a['body'].lower()}" + (f", lately {a['feel'].lower()}" if a.get("feel") else "") + ".")
    lines.append("I'll draft it from your last 12 weeks of training, your load and readiness today, your check-ins and "
                 "your training note. Anything else you want from this plan, like a race on the way, a week you're "
                 f"away, or how you like to train? Tell me, or tap {DRAFT_IT}.")
    return " ".join(lines)


def ask_confirm(o: dict, a: dict) -> dict:
    return coach(confirm_text(o, a), "confirm", [g | {"value": None} for g in QUESTIONS["confirm"]["groups"]])


def next_step(step: str) -> str | None:
    i = STEPS.index(step)
    return STEPS[i + 1] if i + 1 < len(STEPS) else None


# Reading the objective from your own words --------------------------------------------------

MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
NUM = r"(\d[\d,]*(?:\.\d+)?)"


def _n(s: str) -> float:
    return float(s.replace(",", ""))


def _future(month: int, day: int, today: date) -> date | None:
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    return d if d > today else date(today.year + 1, month, day)


def parse_date(text: str, today: date) -> date | None:
    t = text.lower()
    if m := re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    if m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", t):
        if m[3]:
            try:
                return date(int(m[3]), MONTHS[m[1]], int(m[2]))
            except ValueError:
                return None
        return _future(MONTHS[m[1]], int(m[2]), today)
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", t):
        return _future(MONTHS[m[2]], int(m[1]), today)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        if m[3]:
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            try:
                return date(y, int(m[1]), int(m[2]))
            except ValueError:
                return None
        return _future(int(m[1]), int(m[2]), today)
    return None


def rules_objective(text: str, today: date) -> dict:
    """Name, date, miles, feet up and down from plain words, as far as simple patterns go."""
    t = text.replace("’", "'")
    out: dict = {}
    if m := re.search(NUM + r"\s*(?:mi|miles?)\b", t, re.IGNORECASE):
        out["distance_mi"] = _n(m[1])
    elif m := re.search(NUM + r"\s*(?:k|km|kilometers?)\b", t, re.IGNORECASE):
        out["distance_mi"] = round(_n(m[1]) / KM_PER_MI, 1)
    feet = [(_n(m[1]), m.group(0).lower()) for m in re.finditer(NUM + r"\s*(?:ft|feet|')(?:\s*(?:up|down|gain|loss|of (?:gain|vert|descent)|vert|climbing|descent|descending|descend))?", t, re.IGNORECASE)]
    for v, s in feet:
        if any(w in s for w in ("down", "loss", "descen")):
            out.setdefault("loss_ft", v)
        else:
            out.setdefault("gain_ft", v)
    if d := parse_date(t, today):
        out["day"] = d.isoformat()
    t = re.sub(r"^\s*(?:i'?m|i am|we'?re)?\s*(?:training|signed up|registered|going|aiming)?\s*(?:for|to (?:run|do|hike))\s+(?:the\s+)?", "", t, flags=re.IGNORECASE)
    name = re.split(r"\s(?:on|in|,|-|–)\s|,|\bon\b|\d{1,2}/|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t, maxsplit=1, flags=re.IGNORECASE)[0]
    name = clean_name(name)
    if name:
        out["name"] = name[:80]
    elif out.get("distance_mi") and (out.get("gain_ft") or out.get("day")):
        out["name"] = f"{out['distance_mi']:.0f} mi objective"
    return out


VERT_WORDS = r"\b(?:climbing|climb|gain|vert|vertical|descent|descend|descending|loss|up|down|of|and|with|elevation)\b"


def clean_name(name: str) -> str | None:
    """An objective's name without the numbers typed after it ("100miles 18000ft climbing"), and
    nothing for a sentence that names no objective ("i want to train for mountains")."""
    name = re.sub(NUM + r"\s*(?:mi|miles?|ft|feet|')(?![a-z])", " ", name, flags=re.IGNORECASE)
    name = re.sub(VERT_WORDS, " ", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip(" .,-–")
    if not name or re.fullmatch(r"[\d\s.,]*", name) or GENERIC_NAME.search(name):
        return None
    return name


# A sentence about training in general, not an objective's name.
GENERIC_NAME = re.compile(r"^(?:i|i'm|im|i am|my|we|just|want|get|be|stay|maybe|not sure|no|nothing|general|"
                          r"fitness|train|training|mountains?|some|something|anything)\b", re.IGNORECASE)
GENERAL = re.compile(r"\b(?:general|overall|base|all[- ]round|mountain) fitness\b|\bno (?:race|objective|event|goal)\b|"
                     r"\bstay (?:fit|in shape)\b|\bget (?:fit|fitter|in shape)\b|\bnothing (?:specific|planned|in mind)\b|"
                     r"\bno specific\b|\btrain for (?:the )?mountains\b|\bjust (?:fitness|training|train|running|run)\b|"
                     r"\bin general\b", re.IGNORECASE)
BLOCK_WEEKS = 12


def general(text: str) -> bool:
    """Training with no race or date in mind."""
    return bool(GENERAL.search(text))


GOAL_WORDS = (
    ("comeback", re.compile(r"\bcom(?:e|ing) ?back\b|\breturn(?:ing)? (?:to|from)\b|\bafter (?:an? |my )?(?:injury|break|"
                            r"time off|layoff|illness|surgery)\b|\bgetting back\b|\bback (?:into|to) (?:running|training)\b|"
                            r"\b(?:been|was) (?:injured|off|sick)\b", re.I)),
    ("rolling", re.compile(r"\bongoing\b|\bno end\b|\brolling\b|\bweek (?:to|by) week\b|\bopen[- ]ended\b|"
                           r"\bindefinite|\bforever\b|\bno (?:end )?date\b", re.I)),
    ("maintain", re.compile(r"\bmaintain|\bstay (?:fit|in shape)\b|\bkeep (?:my )?(?:fitness|shape)\b|\bhold (?:my )?fitness\b|"
                            r"\boff[- ]?season\b", re.I)),
    ("faster", re.compile(r"\b(?:get|getting|run|be) (?:faster|quicker)\b|\bspeed ?up\b|\bimprove (?:my )?(?:pace|speed)\b|"
                          r"\bmore speed\b", re.I)),
    ("base", re.compile(r"\bbase\b|\bbuild (?:up )?(?:my )?(?:miles|mileage|vert|volume|endurance|aerobic)|"
                        r"\bmore (?:miles|vert|volume)\b|\baerobic\b", re.I)),
)
YEAR_WORDS = re.compile(r"\b(?:this|per|a|each|in a|for the) year\b|\byearly\b|\bannual|\bin 20\d\d\b|\bby (?:the )?end of", re.I)
BIG = r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|million|thousand)?\s*"


def _big(n: str, unit: str | None) -> float:
    return _n(n) * {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}.get((unit or "").lower(), 1)


def year_totals(text: str) -> dict | None:
    """Miles and feet for a year, like "1M ft" or "2,000 mi this year"; None when there's neither."""
    ft = re.search(BIG + r"(?:ft|feet|foot|')(?![a-z])", text, re.I)
    mi = re.search(BIG + r"(?:mi|miles?)\b", text, re.I)
    out = {"distance_mi": _big(*mi.groups()) if mi else 0.0, "gain_ft": _big(*ft.groups()) if ft else 0.0}
    return out if out["distance_mi"] or out["gain_ft"] else None


def goal_kind(text: str) -> str | None:
    """A goal with no race day in typed words: a yearly target, coming back, an ongoing plan,
    maintaining, getting faster, or building base (and "general fitness" and the like)."""
    if YEAR_WORDS.search(text) and year_totals(text):
        return "target"
    for kind, words in GOAL_WORDS:
        if words.search(text):
            return kind
    return "base" if general(text) else None


def _avg_week(acts: list[dict], first: date, last: date) -> dict:
    span = [a for a in acts if first.isoformat() <= a["day"] <= last.isoformat() and planner.is_foot(a["sport"])]
    weeks = max(1.0, ((last - first).days + 1) / 7)
    return {k: sum(a[{"miles": "distance_mi"}.get(k, k)] for a in span) / weeks for k in ("miles", "gain_ft", "loss_ft")}


def block_objective(kind: str, acts: list[dict], today: date, totals: dict | None = None) -> dict:
    """A goal with no race day as an objective: weekly targets from your own training (a yearly
    target's totals instead) and the block's last day."""
    base = planner.baseline(acts, today)
    end = monday(today) + timedelta(weeks=planner.ROLLING_WEEKS if kind == "rolling" else BLOCK_WEEKS, days=-1)
    f = {"base": (planner.BASE_GROWTH, planner.BASE_GROWTH), "rolling": (planner.ROLLING_GROWTH,) * 2,
         "faster": (1.0, planner.FASTER_VERT)}.get(kind, (1.0, 1.0))
    ref = base
    if kind == "comeback":
        # Back to where you were before the break: the 12 weeks before the last four, or now if more.
        before = _avg_week(acts, today - timedelta(days=112), today - timedelta(days=29))
        ref = {k: max(base[k], before[k]) for k in ("miles", "gain_ft", "loss_ft")}
    o = {"goal": kind, "name": GOAL_KINDS[kind], "day": end.isoformat(), "looked_up": "block",
         "distance_mi": round(max(8.0, ref["miles"] * f[0]), 1), "gain_ft": round(max(1000.0, ref["gain_ft"] * f[1]), -1)}
    o["loss_ft"] = round(max(1000.0, ref["loss_ft"] * f[1]), -1) if kind != "target" else 0.0
    if kind == "target":
        end = date(today.year, 12, 31)
        if (end - today).days < 14:
            end = date(today.year + 1, 12, 31)
        t = totals or {}
        parts = ([f"{_ft(t['gain_ft'])} ft"] if t.get("gain_ft") else []) + (
            [f"{_ft(t['distance_mi'])} mi"] if t.get("distance_mi") else [])
        o |= {"day": end.isoformat(), "name": f"{' and '.join(parts)} in {end.year}",
              "distance_mi": float(t.get("distance_mi") or 0), "gain_ft": float(t.get("gain_ft") or 0)}
        o["loss_ft"] = o["gain_ft"]
    return o


def block_text(o: dict, acts: list[dict], today: date) -> str:
    """What a goal with no race day becomes, before it's used."""
    b = planner.baseline(acts, today)
    now = f"about {_mi(b['miles'])} mi and {_ft(b['gain_ft'])} ft up a week"
    wk = f"{_mi(o['distance_mi'])} mi and {_ft(o['gain_ft'])} ft up a week"
    kind = o["goal"]
    if kind == "target":
        done = planner.year_done(acts, today)
        end = date.fromisoformat(o["day"])
        weeks = max(1, round(((end - today).days + 1) / 7))
        left = []
        if o["gain_ft"]:
            left.append(f"{_ft(max(0.0, o['gain_ft'] - done['gain_ft']) / weeks)} ft up")
        if o["distance_mi"]:
            left.append(f"{_mi(max(0.0, o['distance_mi'] - done['miles']) / weeks)} mi")
        text = (f"{o['name']}: you've done {_ft(done['gain_ft'])} ft up and {_mi(done['miles'])} mi so far this year, "
                f"so the plan aims for about {' and '.join(left)} a week for the {weeks} weeks left, building there "
                "from your last four weeks inside the load limits.")
    elif kind == "maintain":
        text = f"A {BLOCK_WEEKS}-week block that holds your fitness at {wk}, with a lighter week every fourth week."
    elif kind == "comeback":
        text = (f"A {BLOCK_WEEKS}-week comeback from {now}: it starts easy and builds back to {wk}, where you were "
                "before the break, with a lighter week every third week. If anything hurts above 3 of 5, the call is rest.")
    elif kind == "faster":
        text = (f"A {BLOCK_WEEKS}-week block that keeps you near {wk}, a little less vert than now, and adds a tempo "
                "session each week. You can switch it to track or hill repeats in Training days.")
    elif kind == "rolling":
        text = (f"An ongoing plan with no end date. It looks {planner.ROLLING_WEEKS} weeks ahead, builds gently from "
                f"{now} toward {wk}, and redraws itself each Monday from your latest training.")
    else:
        text = (f"A {BLOCK_WEEKS}-week base block from {now}, building to {wk} with a recovery week every fourth week, "
                "then you can start the next block.")
    return text + " Use this, or tell me a race, a day you have in mind, or other numbers."


def block_line(o: dict) -> str:
    end = date.fromisoformat(o["day"])
    if o["goal"] == "target":
        return f"{o['name']}, through {end:%b} {end.day}"
    if o["goal"] == "rolling":
        return f"an ongoing plan, around {_mi(o['distance_mi'])} mi and {_ft(o['gain_ft'])} ft up a week"
    return (f"{o['name'].lower()}, {_mi(o['distance_mi'])} mi and {_ft(o['gain_ft'])} ft up a week through "
            f"{end:%b} {end.day}")


TRIP = re.compile(r"\b(?:fkt|peak|summit|traverse|trip|thru|loop|tour|r2r2r|rim to rim|ridge|route|expedition|climb)\b", re.I)


def missing(obj: dict) -> list[str]:
    return [k for k, label in (("name", "a name"), ("day", "the date"), ("distance_mi", "the distance"),
                               ("gain_ft", "the feet of climbing")) if not obj.get(k)]


MISSING_TEXT = {"name": "a name", "day": "the date", "distance_mi": "the distance in miles",
                "gain_ft": "the feet of climbing"}


def objective_line(o: dict) -> str:
    d = date.fromisoformat(o["day"])
    loss = o.get("loss_ft") or o["gain_ft"]
    year = f" {d.year}" if d.year != date.today().year else ""
    return (f"{o['name']} on {DAY_NAMES[d.weekday()]}, {d.strftime('%b')} {d.day}{year}, {o['distance_mi']:.1f} mi, "
            f"{o['gain_ft']:,.0f} ft up and {loss:,.0f} ft down")


# Reading a change you ask for after the draft -----------------------------------------------

DAY_WORDS = {name.lower(): i for i, name in enumerate(DAY_NAMES)} | {s.lower(): i for i, s in enumerate(DAY_SHORT)}
DAY_RE = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)s?\b"


def _minutes(text: str) -> int | None:
    if m := re.search(NUM + r"\s*(?:h|hr|hrs|hours?)\b(?:\s*(\d+)\s*(?:m|min))?", text, re.IGNORECASE):
        return int(_n(m[1]) * 60 + (int(m[2]) if m[2] else 0))
    if m := re.search(NUM + r"\s*(?:m|min|mins|minutes)\b", text, re.IGNORECASE):
        return int(_n(m[1]))
    return None


def rules_edits(text: str, today: date | None = None) -> list[dict]:
    """Changes in the common phrasings: the long day, adding or dropping a day, time limits, more
    or less miles or vert in a numbered week (or a recovery week), workouts added or dropped, and
    one-off rest days."""
    t = text.lower()
    edits: list[dict] = []
    rest, t = rules_rest_days(t, today or date.today())
    edits += rest
    if m := re.search(r"long (?:day|run)[^.]*?\b(?:to|on)\s+" + DAY_RE, t):
        edits.append({"kind": "long_day", "day": DAY_WORDS[m[1]]})
    for m in re.finditer(r"\b(?:no|not|can't|cannot|can not|drop|skip|remove)\b[^.]*?" + DAY_RE, t):
        if "long" not in m.group(0):
            edits.append({"kind": "drop_day", "day": DAY_WORDS[m[1]]})
    for m in re.finditer(r"\b(?:add|also|can (?:now )?(?:run|train))\b[^.]*?" + DAY_RE, t):
        edits.append({"kind": "add_day", "day": DAY_WORDS[m[1]]})
    if "weekday" in t and (v := _minutes(t.split("weekday", 1)[0][-40:] + t.split("weekday", 1)[1][:40])):
        edits.append({"kind": "weekday_minutes", "minutes": v})
    if "weekend" in t and (v := _minutes(t.split("weekend", 1)[0][-40:] + t.split("weekend", 1)[1][:40])):
        edits.append({"kind": "weekend_minutes", "minutes": v})
    picks = rules_workouts(t)
    if picks:
        edits += picks
    elif w := rules_workout(t):
        edits.append({"kind": "workout", "workout": w})
    if re.search(r"back[- ]to[- ]back", t):
        edits.append({"kind": "back_to_back", "on": not re.search(r"\b(?:no|drop|stop|without|don't|do not|off)\b[^.]*back[- ]to[- ]back", t)})
    if m := re.search(r"\bweek\s+(\d{1,2})\b[^.]*\brecovery\b|\brecovery\b[^.]*\bweek\s+(\d{1,2})\b", t):
        edits.append({"kind": "week", "week": int(m[1] or m[2]), "dims": ["miles", "vert"], "factor": RECOVERY_WEEK})
    elif m := re.search(r"\b(less|fewer|lower|reduce|cut|easier|lighter|more|increase|add|bigger|harder)\b(.*?)\bweek\s+(\d{1,2})\b", t):
        less = m[1] in ("less", "fewer", "lower", "reduce", "cut", "easier", "lighter")
        what = m[2]
        dims = [d for d, words in (("miles", ("mile", "mileage", "distance", "volume")),
                                   ("vert", ("vert", "climb", "gain", "feet", "ft", "descent", "volume"))) if any(w in what for w in words)]
        edits.append({"kind": "week", "week": int(m[3]), "dims": dims or ["miles", "vert"], "factor": LESS if less else MORE})
    return edits


SPEED_WORDS = r"(?:track|intervals?|speed ?work|speed|repeats|reps|tempo|threshold|fartlek|progression|workouts?|quality|(?:race|goal|marathon) pace)"


def rules_workout(t: str) -> str | None:
    """A weekly speed session asked for, or dropped: track, tempo or hill repeats, or none."""
    if not re.search(SPEED_WORDS, t):
        return None
    if re.search(r"\b(?:no|drop|remove|stop|without|skip|cut|don't want|do not want)\b[^.]*?" + SPEED_WORDS, t):
        return "none"
    if re.search(r"hill (?:repeats|reps|intervals|sprints)|uphill (?:repeats|intervals)", t):
        return "hill_repeats"
    if re.search(r"(?:race|goal|marathon) pace", t):
        return "race_pace"
    if re.search(r"fartlek", t):
        return "fartlek"
    if re.search(r"progression", t):
        return "progression"
    if re.search(r"threshold|cruise", t):
        return "threshold"
    if re.search(r"tempo", t):
        return "tempo"
    return "track"


PICK_TEXT = {**WORKOUTS, **HILL_SESSIONS, "strides": "Strides", "strength": "Strength"}


def apply_edits(edits: list[dict], c: dict, weeks: list[dict], ptype: str = "mountain_ultra") -> tuple[dict, list[str], list[str]]:
    """The constraints with the asked-for changes made, what changed in words, and anything that
    couldn't be done and why. Week numbers count from this week as week 1. Workouts added or
    dropped start from the athlete's picks, or the plan type's (`ptype`) when they have none.
    One-off rest days aren't constraints: they go on the calendar, so they're left out here."""
    c = {**c, "days": list(c["days"]), "adjust": list(c.get("adjust") or [])}
    done, skipped = [], []
    for e in edits:
        k = e.get("kind")
        if k == "long_day":
            d = e["day"]
            if d not in c["days"]:
                c["days"] = sorted(c["days"] + [d])
            c["long_day"] = d
            done.append(f"long day on {DAY_NAMES[d]}")
        elif k == "add_day" and e["day"] not in c["days"]:
            c["days"] = sorted(c["days"] + [e["day"]])
            done.append(f"{DAY_NAMES[e['day']]} added")
        elif k == "drop_day" and e["day"] in c["days"]:
            if len(c["days"]) == 1:
                skipped.append("you need at least one training day")
                continue
            c["days"] = [d for d in c["days"] if d != e["day"]]
            if c["long_day"] == e["day"]:
                c["long_day"] = 5 if 5 in c["days"] else 6 if 6 in c["days"] else c["days"][-1]
                done.append(f"long day moved to {DAY_NAMES[c['long_day']]}")
            done.append(f"no runs on {DAY_NAMES[e['day']]}")
        elif k in ("weekday_minutes", "weekend_minutes"):
            v = e.get("minutes")
            lo, hi = (15, 600) if k == "weekday_minutes" else (15, 900)
            if not v or not lo <= v <= hi:
                skipped.append(f"{'weekday' if k.startswith('weekday') else 'weekend'} time has to be {lo} to {hi} min")
                continue
            c["weekday_min" if k == "weekday_minutes" else "weekend_min"] = int(v)
            done.append(f"{'weekdays' if k == 'weekday_minutes' else 'weekend days'} up to {int(v)} min")
        elif k == "workout":
            w = e.get("workout")
            if w not in (*WORKOUTS, "none"):
                continue
            c["workout"] = w
            c["workouts"] = [] if w == "none" else [w]
            done.append("no speed work" if w == "none" else
                        f"{WORKOUTS[w].lower()} once a week in build and taper weeks, on a weekday away from the long day")
        elif k in ("add_workout", "drop_workout") and e.get("workout") in PICKABLE:
            w, add = e["workout"], k == "add_workout"
            name = PICK_TEXT[w].lower()
            if w in ("strides", "strength"):
                c[w] = add
            else:
                key = "workouts" if w in WORKOUTS else "hill_sessions"
                d = planner.default_workouts(ptype)
                now = list(c.get(key) if c.get(key) is not None else
                           ([c["workout"]] if key == "workouts" and c.get("workout") in WORKOUTS else d[key]))
                if add == (w in now):
                    skipped.append(f"{name} {'is already' if add else 'is not'} in the plan")
                    continue
                c[key] = now + [w] if add else [x for x in now if x != w]
                if key == "workouts":
                    c["workout"] = c["workouts"][0] if c["workouts"] else "none"
            done.append(f"{name} {'added' if add else 'taken out'}")
        elif k == "back_to_back":
            c["back_to_back"] = bool(e.get("on"))
            done.append("back-to-back long days " + ("on" if c["back_to_back"] else "off"))
        elif k == "week":
            n = int(e.get("week") or 0)
            if not 1 <= n <= len(weeks):
                skipped.append(f"the plan has weeks 1 to {len(weeks)}")
                continue
            start = weeks[n - 1]["start"]
            dims = [d for d in e.get("dims", []) if d in ("miles", "vert")] or ["miles", "vert"]
            c["adjust"] = [a for a in c["adjust"] if not (a["week"] == start and set(a["dims"]) & set(dims))]
            c["adjust"].append({"week": start, "dims": dims, "factor": e.get("factor", LESS)})
            f = e.get("factor", LESS)
            done.append(f"week {n} a recovery week" if f == RECOVERY_WEEK else
                        f"{'less' if f < 1 else 'more'} {' and '.join(dims)} in week {n}")
    return c, done, skipped


EDIT_HELP = ("I can move your long day, add or drop a training day, change how long you can go on weekdays or "
             "weekends, add or drop a workout (like \"add hill sprints\" or \"no strength\"), turn back-to-back long days "
             "on or off, give a single week more or less miles or vert or make it a recovery week, like \"less vert in "
             "week 3\", or take one day off, like \"skip this Saturday\". Or tap a quick change. To change the objective "
             "itself, use Edit objective.")


def _short(text: str, most: int = 80) -> str:
    """The first sentence of text, cut at a word under `most` characters."""
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].rstrip(".!? ")
    if len(first) <= most:
        return first
    return first[:most].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def cant_text(asked: str | None) -> str:
    """The reply when nothing asked for is a change the plan can make."""
    if asked:
        return f"I can't change that in the plan yet ({_short(asked)}). " + EDIT_HELP
    return "I didn't catch a change I can make. " + EDIT_HELP


QUESTION = re.compile(r"^\s*(?:what|what's|how|when|why|where|which|who|can i|could i|will i|would i|should i|"
                      r"am i|is|are|does|did|is it|tell me|explain)\b", re.I)
FINISH = re.compile(r"\b(?:finish|finishing|time|how long|hours?|pace|cutoff|cut-off)\b", re.I)


def is_question(text: str) -> bool:
    """A question about the plan or the objective, rather than a change to it."""
    # A leading "do" asks only with a question mark: "Do less vert next week" is a change.
    return text.strip().endswith("?") or bool(QUESTION.match(text))


def finish_text(plan: dict, name: str) -> str:
    """The engine's finish-time estimate for the objective, with what it rests on."""
    dem, p = plan["demand"], plan["pace"]
    t = dem["time"]
    basis = (f"your pace over the last 8 weeks, {p['min_per_effort_mi']:.1f} min per effort mile"
             if p.get("learned") else "a default pace until there are 3 or more foot days to learn yours")
    return (f"On {basis}, {name} ({_mi(dem['distance_mi'])} mi with {_ft(dem['gain_ft'])} ft up, "
            f"{dem['effort_mi']:.0f} effort mi) works out to about {t['low_h']:.0f} to {t['high_h']:.0f} h of moving "
            "time, slowing as the hours go. Aid stations and sleep come on top. It's a training-pace estimate: race "
            "effort is usually a little faster.")


def week_label(start: str) -> str:
    d = date.fromisoformat(start)
    return f"{d.strftime('%b')} {d.day}"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# What the coach says about a draft. Every number is the engine's, written here, not by a model.

def _mi(v: float) -> str:
    return f"{v:.1f}"


def _ft(v: float) -> str:
    return f"{round(v):,}"


def notes(a: dict) -> str:
    """The answers that don't set a number, kept with the training days as notes."""
    parts = [f"Goal: {a['goal']}" + (f" ({a['goal_note']})" if a.get("goal_note") and a["goal_note"] != a["goal"] else "")
             if a.get("goal") else "",
             f"Vert near home: {a['terrain']}" if a.get("terrain") else "",
             f"Body: {a['body']}" + (f" ({a['body_note']})" if a.get("body_note") and a["body_note"] != a["body"] else "")
             if a.get("body") else "",
             f"Lately: {a['feel']}" if a.get("feel") else "",
             f"Also wants: {a['wants']}" if a.get("wants") else ""]
    return ". ".join(p for p in parts if p)[:500]


def context(a: dict) -> dict:
    """The tap answers the coach may read when it explains the plan. Typed notes stay out."""
    return {k: a[k] for k in ("goal", "terrain", "body", "feel") if a.get(k)}


def full_context(a: dict) -> dict:
    """Every answer, typed words included, for the coach that builds and checks the plan: it
    should know everything the athlete told it before it drafts."""
    out = context(a) | {k: a[k] for k in ("speed", "hills", "extras") if k in a}
    for k, label in (("goal_note", "goal_in_their_words"), ("terrain_note", "terrain_in_their_words"),
                     ("body_note", "body_in_their_words"), ("workouts_note", "workouts_in_their_words"),
                     ("wants", "what_else_they_want")):
        if a.get(k):
            out[label] = a[k]
    return out


def lighter_start(today: date) -> list[dict]:
    """After "tired lately", this week and next come in lighter."""
    w0 = monday(today)
    return [{"week": (w0 + timedelta(weeks=i)).isoformat(), "dims": ["miles", "vert"], "factor": LESS} for i in (0, 1)]


def _weekday(iso: str) -> str:
    return DAY_NAMES[date.fromisoformat(iso).weekday()]


BUILDING_TEXT = ("The AI coach is drafting your plan from your training, goals and answers, then checking it a "
                 "second time. It shows up here when it's ready, usually in a few minutes.")


def built_text(plan: dict, mode: str = "setup", a: dict | None = None) -> str:
    """The message when the coach's build of a draft finishes: the coach's plan, or the engine's
    draft when the coach couldn't build one."""
    head = ("The AI coach's plan is ready." if plan.get("builder") == "coach"
            else "The AI coach couldn't build a plan this time, so here's the engine's draft.")
    if mode == "change":
        return "\n".join([head, *_built_by(plan), *_change_lines(plan.get("changes") or []),
                          "Accept the new draft, or keep changing it."])
    c = plan.get("constraints") or {}
    return head + " " + draft_summary(plan, plan["objective"]["name"], c.get("long_day", 5), a or {})


def _built_by(plan: dict) -> list[str]:
    """How the AI coach shaped the draft, what its second check found, and how many weeks the
    engine held to its limits."""
    if plan.get("builder") != "coach":
        return []
    out = [f"The AI coach built this one: {plan['approach'].strip()}" if plan.get("approach") else
           "The AI coach built this one."]
    if (ck := plan.get("check")) and ck.get("summary"):
        out.append(("Its second check changed it: " if ck.get("revised") else "Its second check: ") + ck["summary"])
    if n := plan.get("held_weeks"):
        out.append(f"The engine held {n} week{'s' if n != 1 else ''} it asked for to the 10% build limit.")
    return out


def block_summary(plan: dict, name: str, long_day: int, a: dict) -> str:
    """The draft for a goal with no race day."""
    weeks, b = plan["weeks"], plan["baseline"]
    goal = plan.get("goal", "base")
    full = weeks[1:] or weeks
    pk = {k: max(w[k] for w in full) for k in ("miles", "gain_ft", "loss_ft")}
    span = ("an ongoing plan, looking " + f"{len(weeks)} weeks ahead") if goal == "rolling" else f"{len(weeks)} weeks: {name}"
    move = ("holds about" if all(abs(pk[k] - b[k]) <= b[k] * 0.05 + 1 for k in ("miles", "gain_ft"))
            else "steps down to" if pk["miles"] < b["miles"] * 0.95 and pk["gain_ft"] < b["gain_ft"] * 0.95
            else "builds to")
    lines = [f"Here's your draft: {span}. From your last four weeks, about {_mi(b['miles'])} mi and {_ft(b['gain_ft'])} ft "
             f"up a week, it {move} {_mi(pk['miles'])} mi with {_ft(pk['gain_ft'])} ft up and {_ft(pk['loss_ft'])} ft down, "
             f"with a recovery week every {'third' if goal == 'comeback' else 'fourth'} week."] + _built_by(plan)
    longs = [w["long"] for w in weeks if w.get("long")]
    if big := max(longs, key=lambda l: l["effort_mi"], default=None):
        lines.append(f"Your biggest long day is {_mi(big['miles'])} mi with {_ft(big['gain_ft'])} ft up, on a {DAY_NAMES[long_day]}.")
    speed = next((w["workout"] for w in weeks if w.get("workout")), None)
    if speed in WORKOUTS:
        lines.append(f"Each week has one {WORKOUTS[speed].lower()} session.")
    if goal == "rolling":
        lines.append("Each Monday it redraws the weeks ahead from your latest training, so it never runs out.")
    if goal == "target" and not plan["reachable"]:
        lines.append("At the pace the load limits allow, it falls short of the year's target; the plan won't loosen them.")
    if a.get("feel") == "Tired lately":
        lines.append("Since training has felt tiring lately, this week and next are a little lighter.")
    if a.get("body") in BODY[1:] or goal == "comeback":
        lines.append("Log anything that hurts in the morning check-in: above 3 of 5, the call is rest.")
    if a.get("terrain") == TERRAIN[2]:
        lines.append("With little vert near home, get the climbing on a treadmill incline or stairs.")
    lines.append("Accept it, or tell me what to change. " + CHANGE_EXAMPLES)
    return " ".join(lines)


def draft_summary(plan: dict, name: str, long_day: int, a: dict) -> str:
    if not planner.is_event(plan["objective"]):
        return block_summary(plan, name, long_day, a)
    longs = [w["long"] for w in plan["weeks"] if w.get("long")]
    big = max(longs, key=lambda l: l["effort_mi"], default=None)
    b = plan["baseline"]
    # The biggest week the plan actually reaches, which time and load limits can hold under its target.
    full = [w for w in plan["weeks"] if w["focus"] in ("build", "peak")][1:] or plan["weeks"]
    pk = {k: max(w[k] for w in full) for k in ("miles", "gain_ft", "loss_ft")}
    if all(pk[k] <= b[k] * 1.02 for k in ("miles", "gain_ft")):
        lines = [f"Here's your draft: {len(plan['weeks'])} weeks to {name}. Your last four weeks, about {_mi(b['miles'])} mi "
                 f"and {_ft(b['gain_ft'])} ft up a week, already carry what this objective needs, so the plan doesn't "
                 f"build: it holds about {_mi(pk['miles'])} mi with {_ft(pk['gain_ft'])} ft up and {_ft(pk['loss_ft'])} ft "
                 "down, with recovery weeks and a taper."]
    else:
        lines = [f"Here's your draft: {len(plan['weeks'])} weeks to {name}. You start from your last four weeks, about "
                 f"{_mi(b['miles'])} mi and {_ft(b['gain_ft'])} ft up a week, and build to a peak of {_mi(pk['miles'])} mi "
                 f"with {_ft(pk['gain_ft'])} ft up and {_ft(pk['loss_ft'])} ft down."]
    lines += _built_by(plan)
    if big and big.get("second"):
        two = big["second"]
        lines.append(f"Your objective is long enough for back-to-back long days: the biggest pair is {_mi(big['miles'])} mi "
                     f"with {_ft(big['gain_ft'])} ft up on a {_weekday(big['day'])} and {_mi(two['miles'])} mi "
                     f"with {_ft(two['gain_ft'])} ft up on the {_weekday(two['day'])} next to it.")
    elif big:
        lines.append(f"Your biggest long day is {_mi(big['miles'])} mi with {_ft(big['gain_ft'])} ft up, on a {DAY_NAMES[long_day]}.")
    rtype = plan.get("race_type")
    tapers = sum(w["focus"] == "taper" for w in plan["weeks"])
    taper = "a one-week taper" if tapers == 1 else f"a {tapers}-week taper" if tapers else ""
    speed = next((w["workout"] for w in plan["weeks"] if w.get("workout")), None)
    if rtype == "road":
        lines.append("It's a road plan: easy miles with less vert than a trail build"
                     + (f", one {WORKOUTS[speed].lower()} session a week" if speed in WORKOUTS else "")
                     + (f" and {taper}" if taper else "") + ".")
    elif rtype in ("hundred", "multiday"):
        lines.append(f"For a {'multi-day' if rtype == 'multiday' else '100-mile'} race the long days stay well short "
                     "of the race: they practice power-hiking, eating from the first hour, and, every third build week, "
                     "moving in the dark" + (" with a short sleep stop" if rtype == "multiday" else "")
                     + (f". Then {taper}." if taper else "."))
    if not plan["reachable"]:
        opts = plan.get("options") or {}
        lines.append("It can't get you all the way there by this date inside the load limits, and it won't loosen them."
                     + (f" It can by {week_label(opts['later'])}." if opts.get("later") else ""))
    if a.get("feel") == "Tired lately":
        lines.append("Since training has felt tiring lately, this week and next are a little lighter.")
    if a.get("body") in BODY[1:]:
        lines.append("With something hurting, log it in the morning check-in: above 3 of 5, the call is rest.")
    if a.get("terrain") == TERRAIN[2]:
        lines.append("With little vert near home, get the climbing on a treadmill incline or stairs.")
    if a.get("goal") == GOALS[1]:
        lines.append("The plan builds the miles and vert the day needs; it doesn't set paces.")
    lines.append("Accept it, or tell me what to change. " + CHANGE_EXAMPLES)
    return " ".join(lines)


def change_text(done: list[str], skipped: list[str], changes: list[dict] | None) -> str:
    """What a change did. changes is None while the AI coach is still drafting the new plan."""
    out = []
    if done:
        out.append("Done: " + "; ".join(done) + ".")
    if skipped:
        out.append("I couldn't do all of it: " + "; ".join(skipped) + ".")
    if done and changes is not None:
        out += _change_lines(changes)
        out.append("Accept the new draft, or keep changing it.")
    return "\n".join(out)


def _change_lines(changes: list[dict]) -> list[str]:
    out = [] if changes else ["No week moves by more than 5%."]
    for c in changes[:4]:
        a, b = c["after"], c["before"]
        out.append(f"Week of {week_label(c['start'])}: {_mi(a['miles'])} mi, {_ft(a['gain_ft'])} ft up"
                   + (f" (was {_mi(b['miles'])} mi, {_ft(b['gain_ft'])} ft up)." if b else "."))
    if len(changes) > 4:
        out.append(f"And {len(changes) - 4} more weeks.")
    return out


def _join_words(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
