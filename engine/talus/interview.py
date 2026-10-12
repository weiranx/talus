"""Planning with the coach: a short conversation that fills the same inputs as the plan form.

The coach asks six questions, most answered with a tap. Its questions and confirmations are
written here, so they cost nothing; only free text (the objective in your own words, or a change
you ask for after the draft) goes to the cheaper model, and only to be read into fields. The
engine drafts every number. Without a model, simple rules read the common phrasings.
Distances are miles, vert is feet.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .plan import DAY_NAMES, WORKOUTS

DAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
KM_PER_MI = 1.609344
STEPS = ("objective", "goal", "days", "time", "terrain", "body")

GOALS = ("Finish strong", "A time goal", "Just get round")
TERRAIN = ("Big climbs nearby", "Rolling hills", "Mostly flat or treadmill")
BODY = ("Nothing to note", "A small niggle", "An injury I'm managing")
FEEL = ("Fresh", "Normal", "Tired lately")
WEEKDAY_MIN = {"45 min": 45, "1 h": 60, "1 h 30": 90, "2 h": 120}
WEEKEND_MIN = {"2 h": 120, "3 h": 180, "4 h": 240, "6 h": 360, "8 h": 480}
LESS, MORE = 0.85, 1.10  # "less" or "more" for a single week; the engine still checks the limits

QUESTIONS = {
    "objective": {"text": "What are you training for? Tell me the name, the date, the distance and the vert, "
                          "like \"Baldy 50k on Jan 2, 31 mi, 8,000 ft\". Or upload the route's GPX.",
                  "groups": []},
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
        first = coach(f"You're set up for {objective_line(current)}. Keep it, or tell me what else you're "
                      "training for. You can also upload the route's GPX.", "objective",
                      [{"key": "keep", "options": ["Keep it"], "value": None}])
    return {"messages": [coach("Let's build your plan. Six quick questions, and I'll draft it from your "
                               "last four weeks of training."), first],
            "step": "objective", "mode": "setup", "answers": {}, "objective": {}, "defaults": days_defaults(c)}


def change(name: str, c: dict) -> dict:
    """A conversation about changing the plan you have."""
    return {"messages": [coach(f"What would you like to change in your plan for {name}? " + CHANGE_EXAMPLES, "draft")],
            "step": "draft", "mode": "change", "answers": {}, "objective": {}, "defaults": days_defaults(c)}


CHANGE_EXAMPLES = "For example: \"move my long day to Sunday\", \"I can't run Tuesdays\" or \"less vert in week 3\"."


def said(step: str, answers: dict) -> str:
    """Chip answers as the athlete's line in the conversation."""
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
        parts.append("Use these")
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
        if not 15 <= out["weekday_min"] <= 600 or not 15 <= out["weekend_min"] <= 900:
            return None, "Weekdays can be 15 to 600 min and weekend days 15 to 900 min."
        return out, None
    return None, None


def from_intake(r: dict, today: date) -> dict:
    """The model's reading of the objective, checked and in miles and feet."""
    out: dict = {}
    if isinstance(r.get("name"), str) and r["name"].strip():
        out["name"] = r["name"].strip()[:80]
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
        elif k == "week" and isinstance(e.get("week"), int) and e.get("direction") in ("less", "more"):
            out.append({"kind": "week", "week": e["week"], "dims": [d for d in e.get("dims") or [] if d in ("miles", "vert")]
                        or ["miles", "vert"], "factor": LESS if e["direction"] == "less" else MORE})
    return out


def days_defaults(c: dict) -> dict:
    return {"days": [DAY_SHORT[d] for d in c.get("days", [])], "long_day": DAY_SHORT[c.get("long_day", 5)],
            "weekday": next((k for k, v in WEEKDAY_MIN.items() if v == c.get("weekday_min")), None),
            "weekend": next((k for k, v in WEEKEND_MIN.items() if v == c.get("weekend_min")), None)}


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
    feet = [(_n(m[1]), m.group(0).lower()) for m in re.finditer(NUM + r"\s*(?:ft|feet|')(?:\s*(?:up|down|gain|loss|of (?:gain|vert|descent)|vert|climbing|descent))?", t, re.IGNORECASE)]
    for v, s in feet:
        if any(w in s for w in ("down", "loss", "descent")):
            out.setdefault("loss_ft", v)
        else:
            out.setdefault("gain_ft", v)
    if d := parse_date(t, today):
        out["day"] = d.isoformat()
    t = re.sub(r"^\s*(?:i'?m|i am|we'?re)?\s*(?:training|signed up|registered|going|aiming)?\s*(?:for|to (?:run|do|hike))\s+(?:the\s+)?", "", t, flags=re.IGNORECASE)
    name = re.split(r"\s(?:on|in|,|-|–)\s|,|\bon\b|\d{1,2}/|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", t, maxsplit=1, flags=re.IGNORECASE)[0]
    name = name.strip(" .,-–")
    if name and not re.fullmatch(r"[\d\s.,]*", name):
        out["name"] = name[:80]
    return out


def missing(obj: dict) -> list[str]:
    return [k for k, label in (("name", "a name"), ("day", "the date"), ("distance_mi", "the distance"),
                               ("gain_ft", "the feet of climbing")) if not obj.get(k)]


MISSING_TEXT = {"name": "a name", "day": "the date", "distance_mi": "the distance in miles",
                "gain_ft": "the feet of climbing"}


def objective_line(o: dict) -> str:
    d = date.fromisoformat(o["day"])
    loss = o.get("loss_ft") or o["gain_ft"]
    return (f"{o['name']} on {DAY_NAMES[d.weekday()]}, {d.strftime('%b')} {d.day}, {o['distance_mi']:.1f} mi, "
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


def rules_edits(text: str) -> list[dict]:
    """Changes in the common phrasings: the long day, adding or dropping a day, time limits, and
    more or less miles or vert in a numbered week."""
    t = text.lower()
    edits: list[dict] = []
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
    if w := rules_workout(t):
        edits.append({"kind": "workout", "workout": w})
    if m := re.search(r"\b(less|fewer|lower|reduce|cut|easier|lighter|more|increase|add|bigger|harder)\b(.*?)\bweek\s+(\d{1,2})\b", t):
        less = m[1] in ("less", "fewer", "lower", "reduce", "cut", "easier", "lighter")
        what = m[2]
        dims = [d for d, words in (("miles", ("mile", "mileage", "distance", "volume")),
                                   ("vert", ("vert", "climb", "gain", "feet", "ft", "descent", "volume"))) if any(w in what for w in words)]
        edits.append({"kind": "week", "week": int(m[3]), "dims": dims or ["miles", "vert"], "factor": LESS if less else MORE})
    return edits


SPEED_WORDS = r"(?:track|intervals?|speed ?work|speed|repeats|reps|tempo|threshold|workouts?|quality)"


def rules_workout(t: str) -> str | None:
    """A weekly speed session asked for, or dropped: track, tempo or hill repeats, or none."""
    if not re.search(SPEED_WORDS, t):
        return None
    if re.search(r"\b(?:no|drop|remove|stop|without|skip|cut|don't want|do not want)\b[^.]*?" + SPEED_WORDS, t):
        return "none"
    if re.search(r"hill (?:repeats|reps|intervals|sprints)|uphill (?:repeats|intervals)", t):
        return "hill_repeats"
    if re.search(r"tempo|threshold", t):
        return "tempo"
    return "track"


def apply_edits(edits: list[dict], c: dict, weeks: list[dict]) -> tuple[dict, list[str], list[str]]:
    """The constraints with the asked-for changes made, what changed in words, and anything that
    couldn't be done and why. Week numbers count from this week as week 1."""
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
            done.append("no speed work" if w == "none" else
                        f"{WORKOUTS[w].lower()} once a week in build and taper weeks, on a weekday away from the long day")
        elif k == "week":
            n = int(e.get("week") or 0)
            if not 1 <= n <= len(weeks):
                skipped.append(f"the plan has weeks 1 to {len(weeks)}")
                continue
            start = weeks[n - 1]["start"]
            dims = [d for d in e.get("dims", []) if d in ("miles", "vert")] or ["miles", "vert"]
            c["adjust"] = [a for a in c["adjust"] if not (a["week"] == start and set(a["dims"]) & set(dims))]
            c["adjust"].append({"week": start, "dims": dims, "factor": e.get("factor", LESS)})
            done.append(f"{'less' if e.get('factor', LESS) < 1 else 'more'} {' and '.join(dims)} in week {n}")
    return c, done, skipped


EDIT_HELP = ("I can move your long day, add or drop a training day, change how long you can go on weekdays or "
             "weekends, add a weekly speed session (track intervals, tempo or hill repeats), or give a single week "
             "more or less miles or vert, like \"less vert in week 3\". To change the objective itself, use Edit "
             "objective.")


def cant_text(asked: str | None) -> str:
    """The reply when nothing asked for is a change the plan can make."""
    if asked:
        return f"I can't change that in the plan yet ({asked[:120].rstrip('.')}). " + EDIT_HELP
    return "I didn't catch a change I can make. " + EDIT_HELP


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
             f"Lately: {a['feel']}" if a.get("feel") else ""]
    return ". ".join(p for p in parts if p)[:500]


def context(a: dict) -> dict:
    """The tap answers the coach may read when it explains the plan. Typed notes stay out."""
    return {k: a[k] for k in ("goal", "terrain", "body", "feel") if a.get(k)}


def lighter_start(today: date) -> list[dict]:
    """After "tired lately", this week and next come in lighter."""
    w0 = monday(today)
    return [{"week": (w0 + timedelta(weeks=i)).isoformat(), "dims": ["miles", "vert"], "factor": LESS} for i in (0, 1)]


def _weekday(iso: str) -> str:
    return DAY_NAMES[date.fromisoformat(iso).weekday()]


def draft_summary(plan: dict, name: str, long_day: int, a: dict) -> str:
    longs = [w["long"] for w in plan["weeks"] if w.get("long")]
    big = max(longs, key=lambda l: l["effort_mi"], default=None)
    b, pk = plan["baseline"], plan["peak"]
    if all(pk[k] <= b[k] * 1.02 for k in ("miles", "gain_ft")):
        lines = [f"Here's your draft: {len(plan['weeks'])} weeks to {name}. Your last four weeks, about {_mi(b['miles'])} mi "
                 f"and {_ft(b['gain_ft'])} ft up a week, already carry what this objective needs, so the plan doesn't "
                 f"build: it holds about {_mi(pk['miles'])} mi with {_ft(pk['gain_ft'])} ft up and {_ft(pk['loss_ft'])} ft "
                 "down, with recovery weeks and a taper."]
    else:
        lines = [f"Here's your draft: {len(plan['weeks'])} weeks to {name}. You start from your last four weeks, about "
                 f"{_mi(b['miles'])} mi and {_ft(b['gain_ft'])} ft up a week, and build to a peak of {_mi(pk['miles'])} mi "
                 f"with {_ft(pk['gain_ft'])} ft up and {_ft(pk['loss_ft'])} ft down."]
    if big and big.get("second"):
        two = big["second"]
        lines.append(f"Your objective is long enough for back-to-back long days: the biggest pair is {_mi(big['miles'])} mi "
                     f"with {_ft(big['gain_ft'])} ft up on a {_weekday(big['day'])} and {_mi(two['miles'])} mi "
                     f"with {_ft(two['gain_ft'])} ft up on the {_weekday(two['day'])} next to it.")
    elif big:
        lines.append(f"Your biggest long day is {_mi(big['miles'])} mi with {_ft(big['gain_ft'])} ft up, on a {DAY_NAMES[long_day]}.")
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


def change_text(done: list[str], skipped: list[str], changes: list[dict]) -> str:
    out = []
    if done:
        out.append("Done: " + "; ".join(done) + ".")
    if skipped:
        out.append("I couldn't do all of it: " + "; ".join(skipped) + ".")
    if done:
        if not changes:
            out.append("No week moves by more than 5%.")
        for c in changes[:4]:
            a, b = c["after"], c["before"]
            out.append(f"Week of {week_label(c['start'])}: {_mi(a['miles'])} mi, {_ft(a['gain_ft'])} ft up"
                       + (f" (was {_mi(b['miles'])} mi, {_ft(b['gain_ft'])} ft up)." if b else "."))
        if len(changes) > 4:
            out.append(f"And {len(changes) - 4} more weeks.")
        out.append("Accept the new draft, or keep changing it.")
    return "\n".join(out)


def _join_words(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
