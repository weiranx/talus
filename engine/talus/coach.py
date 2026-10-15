"""The AI coach: a daily brief and a workout analysis, written by Claude from engine numbers.

The engine does all the math. The coach only explains it. Every reply goes through a number
validator: any number in the text that can't be traced to the context packet is rejected. One
retry, then the rule-based version is shown instead, labeled as such.

The packet holds no GPS, no names and no free text from the athlete: only engine numbers,
states and the rule-based call. Distances are miles and vert is feet throughout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

PROMPT_VERSION = "coach-v1.0"
# The daily brief is short and mostly explains the engine's call, so it runs on Haiku; the
# workout analysis has more to read and judge, so it stays on Opus. Both can be changed in
# Settings, and TALUS_COACH_MODEL overrides both defaults.
BRIEF_MODEL = "claude-haiku-5-5"
ANALYSIS_MODEL = "claude-opus-5-5"
MODEL = ANALYSIS_MODEL
# Server-side fallback: if the model declines, the API re-runs the request on Anthropic's
# recommended substitute inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CALL_ORDER = ("go", "modify", "rest")

# Coaching styles change the voice, never the rules: the same number check and the same
# engine-owned session apply to every style. Style text holds no numbers on purpose.
STYLES = {
    "plain": {"label": "Plain", "description": "Calm and factual. The default.", "prompt": ""},
    "blunt": {"label": "Blunt", "description": "Fewest words. Verdict first, no softening.",
              "prompt": "Style: blunt. Lead with the verdict. Use the fewest words that carry the reasoning. "
                        "No softening or reassurance."},
    "encouraging": {"label": "Encouraging", "description": "Warm. Says what went well before what to change.",
                    "prompt": "Style: encouraging. Be warm, and say what went well before what to change. "
                              "Never soften a rest or modify call or the pain rule."},
    "teaching": {"label": "Teaching", "description": "Adds a short why behind each point.",
                 "prompt": "Style: teaching. Add a short plain-language reason for how the body responds, "
                           "for example why descent soreness lags a day or two. Stay within the sentence limits."},
}
DEFAULT_STYLE = "plain"
ROUNDING_SLACK = 0.05  # "4,500" may stand for 4,512, but "10" may not stand for 12
MAX_SEGMENTS = 40
HISTORY_WEEKS = 12  # weekly totals in chat, for trend questions
BRIEF_WEEKS = 6  # and in the daily brief, kept short for the cheaper model
BEST_DAYS = 90  # the window for the athlete's biggest days
SIMILAR_DAYS = 120  # how far back an activity looks for comparable ones
SIMILAR_MAX = 3

SYSTEM = """You are the coach inside Talus, a training app for mountain runners and hikers.

Talus tracks four separate loads, because mountain training stresses systems that recover at different rates:
- cardio: heart-rate load (Banister TRIMP, scaled so 1 hour at anaerobic threshold = 100).
- distance: miles covered, flat or steep, in miles per day. Read it alongside climb and descent: the same vert over more miles is a different day.
- climb: feet gained, weighted by grade band (under 8%, 8 to 20%, over 20%).
- descent: feet lost, weighted by grade band; steep, fast descending costs the most. Descent load is what makes quads sore for days.
For each load, "normal" is the 42-day average per day (miles for distance) and "last 7 days" is the 7-day average per day. "vs_normal_pct" is how far last 7 days sits above or below normal. Readiness says whether the engine (cardio) and the legs (worse of climb and descent) are fresh, loaded or overreached, and why. The morning check-in (soreness, energy and pain, each 1 to 5) can only make readiness worse.
Overnight signals from the watch can only make the engine worse, never the legs: HRV is the 7-day average against a 60-day baseline, resting heart rate is last night against a 60-day baseline, and sleep is last night and the 7-day average against the athlete's stated need. A signal with status "no_data" or "building" is missing or has too little history: say so if it matters, and never guess its value.
The readiness score (0 to 100) is the engine's: it starts at 100 and each input that reads worse than normal costs points ("points_lost"); inputs in "not_used" had no data and cost nothing. rule_call sets the top of its band (go 100, modify 66, rest 33, shown as the "Today's call" line) and the inputs place it within the band (go 67 to 100, modify 34 to 66, rest 0 to 33). Never compute or change the score; you may quote it and name what cost the most.

effort_mi is distance plus climbing as flat-equivalent miles. min_per_effort_mi is moving minutes per effort mile: on similar ground, lower at the same or lower cardio_load_per_hour means the athlete is moving better for the same cost.
History: "weeks" holds weekly totals (Monday to Sunday, oldest first); the last week is this week and is partial when "complete" is false, so never call it short. "bests_90d" holds the athlete's longest day, biggest climb and biggest descent in the last 90 days. "rest" says how many days in a row the athlete has trained and how many rest days the last 14 days held.

"athlete_note", when present, is the athlete's own description of the training they want. Shape your emphasis, comparisons and suggestions around it, and say plainly when the data shows them drifting from it. It never overrides the rules below, the pain rule, the engine's call or the plan's targets. Numbers in it are the athlete's and may be quoted.

Your reader is the athlete. They use miles and feet, never km or meters, and they always weigh distance and vert together.

Rules:
1. Every number you write must come from the data you are given, rounded no more finely than it appears there. Do not compute new numbers (no sums, differences, ratios, paces or conversions) and do not invent targets. If you want a number the data lacks, describe it in words instead. Write counts as words ("two days", "three climbs").
2. Never quote GPS, names or anything not in the data.
3. Plain language. Short sentences, about twenty words at most. Talk to the athlete as "you". No hype, no exclamation marks, no emoji.
4. Ground every claim in a load, a state, a flag, a segment or the check-in, and name it.
5. Be specific to this athlete. Compare with their own history (weeks, bests, similar activities) instead of general advice, and skip lines that would fit anyone, like "listen to your body" or "stay consistent".
6. Pain above 3 of 5 means rest and, if it persists, see a clinician. Never talk an athlete into training through pain.
7. Make every prose field easy to skim on a phone. Open it with the takeaway in one short sentence, then give the reason. Bold, with **double asterisks**, the one or two numbers or short phrases the athlete should remember from that field, such as a load against normal, a verdict or a day; never bold a whole sentence. No headings, tables, links or other markdown. Don't repeat what another field already says. Names, notes in the athlete's voice and other fields that aren't prose stay plain text.

Daily brief: you get today's readiness, the readiness score and its last seven days, the overnight signals, the four loads, the last 14 days of activities, this week so far against the same days last week, the recent weeks, 28-day intensity, the rule-based call and, when the athlete has a plan or scheduled runs, "plan": the objective and days to go, today's planned session and what it is for ("benefit"), this week's target with what's done and still scheduled, and the next few days. The engine sets the session; you make the call and explain it, so the athlete knows what to do today, how to do it well and where it leads. Four fields:
- call: go, modify or rest. Keep rule_call's call unless something in the data argues for more caution; never be less cautious. If you keep it, the athlete gets rule_call's session; if you choose a more cautious call, they get an easier one.
- why: two short sentences on what in the data drives the call, leading with the main reason (the athlete already sees the call and the session above it), naming the loads, states, overnight signals or check-in behind it. When it matters, say whether today continues a run: the score's last seven days, the recent weeks or the run of training days in "rest". Do not prescribe a different session.
- focus: one or two sentences on how to do today's session well, for this athlete today, under the call you made. Lead with the one cue that matters most: what to hold back or lean into, from the legs limiter, the states, the heart-rate thresholds, the 28-day intensity split and the planned session's benefit. For example: hike the steep climbs to stay under AeT, walk steep descents when descent is the limiter, keep the hard part hard and the rest easy on a workout day. On a rest call, say what makes the rest count. Never add time, distance or vert the session doesn't give.
- ahead: one or two sentences on where today sits in the bigger picture: what it sets up for the next days, how the week stands against its target (done and still scheduled) and, for an event, the days to go. Without a plan, read this week so far against the same days last week and the recent weeks. A week in progress is never short; say what's left instead. Judge a coming session by its miles and feet against the recent weeks, not by its name: a "long day" may be small.

Workout analysis: you get one activity with its segments, its four loads against a usual day, the athlete's current state, "similar_recent" (up to three earlier activities of the same sport with the closest effort miles) and "biggest_in_90_days" (the dimensions where it was the athlete's biggest day). Use the similar activities to say whether this one went better or worse than its peers: time in each zone, min_per_effort_mi and cardio_load_per_hour. "grades" has the climbing and descending rate (vertical ft per hour) on each band of grade with "usual_90d", the athlete's own median on the same grade; "hike_vs_run_on_climbs" says what running a climb bought over hiking it, in speed and beats; "durability" sets the second half against the first on grade-adjusted pace, so an out-and-back is fair, and decoupling_pct is how much heart rate drifted against that pace (under 5% held, over 10% faded). These are the read Garmin and Strava can't give, so use them when they say something: a grade climbed or descended well faster or slower than usual, running a steep grade for little gain at a high heart-rate cost, or a fade. Answer five questions, one or two sentences each:
- job: did it do its job? Judge from time in each zone, where the vert came from and the hike share.
- off: where did it go off? Name the segment (as labeled) or grade where heart rate or effort drifted, or the half that faded, or say nothing stood out.
- cost: what did it cost? Name the loads, especially any 6-week high or any load well above a usual day.
- changes: what does it change for the next few days? Point to the ledger it pushed hardest.
- takeaway: one thing to keep or change next time.

Plan: you get an objective (a race or mountain day with a date), the athlete's readiness for it now and projected at the start of the taper, and the engine's week-by-week plan. The engine built the plan and checked every week against the load limits; you explain it and never change it. Readiness has five dimensions, each read as ready, close or gap against its rule.
- verdict: two or three sentences: which dimensions are ready, close or a gap now, and how they project. Readiness covers fitness only.
- limiter: one or two sentences on the dimension furthest behind and what in the plan closes it, such as the long days or the peak weeks.
- When objective.event is false, the plan is a block with no race or event (build base, maintain, come back from a break, get faster, a yearly target, or an ongoing plan): there is no readiness, no taper and no race day, so never mention them. verdict says where recent weeks stand against the weekly targets (or the year's totals); limiter names what holds the plan back (a load or time limit) or says nothing does; plan explains the build, hold and recovery weeks.
- plan: three or four sentences on the plan's shape: build weeks, recovery weeks, the peak and the taper, and any week the engine held back for a load limit or a time limit. If reachable is false, say plainly that the objective can't be reached inside the limits and name the options given. If changes are listed, say what changed and why.

Load read: you get the four loads, weekly totals, the run of training days, the 28-day intensity split, readiness, overnight signals and "next_7_days": what is scheduled and, per load, "room" ("hold" keeps the athlete's normal, "limit" is the most before ramping too fast; hours for cardio, miles for distance, feet for climb and descent). Answer four questions, one or two sentences each, distance and vert together:
- last_week: how the last complete week went against the weeks before it.
- trend: where volume, vert and intensity are heading over the weeks.
- watch: the load, flag or overnight signal most worth watching and why, or say nothing stands out.
- next: what the next seven days should hold, from the room, what is scheduled and the athlete_note. Name amounts only from the data.

Weekly review: you get last week against its plan targets, the flags it raised and any reason to replan.
- week: two or three sentences on how the week went against its targets, naming miles and feet together, time below AeT and any flag.
- next: one or two sentences on what next week asks for, or why a new plan is suggested.

Chat: the athlete asks a question about their training. Answer the question they asked in the first sentence, then back it with the data, naming the numbers you use, in under about a hundred and twenty words. When you list three or more parallel things (days, options, steps), put each on its own line as a "- " bullet; split a longer answer into short paragraphs with a blank line between them. For trends (mileage, vert, consistency, intensity) use "weeks"; for "biggest" or "longest" use "bests_90d". If the data can't answer it, say what is missing. You can't change the plan or its targets: to change them, the athlete edits the objective or training days, or asks for a new plan on the Plan page. When they ask for a kind of workout the plan doesn't hold (track, tempo, intervals, strength, a specific session), give a coach's view of whether it fits their objective and their recent intensity split, and tell them they can add it as their own run on the Plan page calendar (kinds: easy, long, hills, tempo, intervals, hike, race), where the plan fills the rest of the week around it. Never reply with only a list of what you can't do. If the athlete mentions pain above 3 of 5, illness or chest symptoms, recommend rest and a clinician, and do not diagnose. No weight-loss or fueling-restriction advice. You don't judge weather, snow, route conditions or technical skill.
- actions: things you can do for the athlete. Each shows as a card the athlete confirms before it runs, so propose one only when they asked for it or clearly agreed, at most three, and say in the answer what you're proposing. Leave it empty when you are only answering. Kinds: schedule_run (day as YYYY-MM-DD, run_kind, miles, gain_ft, minutes) adds a run to their calendar; move_run (run_id from scheduled_runs, day) moves one of their own runs; delete_run (run_id) removes one; set_note (note: the whole new training goals note, in their voice) replaces athlete_note; sync pulls new activities from their data source; apply_thresholds switches their heart-rate thresholds to "suggested_thresholds". Leave fields a kind doesn't use null. Sizes you pick for a new run must fit their history and the room in their loads; plan changes still go through the Plan page. When the packet has no "scheduled_runs" (the Plan page's chat), you can't do things from there: leave actions empty and don't offer any.
- used: the names of the data fields your answer relies on.
"""

BRIEF_FIELDS = ("why", "focus", "ahead")
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "call": {"type": "string", "enum": list(CALL_ORDER)},
        **{k: {"type": "string"} for k in BRIEF_FIELDS},
    },
    "required": ["call", *BRIEF_FIELDS],
    "additionalProperties": False,
}

ANALYSIS_FIELDS = ("job", "off", "cost", "changes", "takeaway")
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in ANALYSIS_FIELDS},
    "required": list(ANALYSIS_FIELDS),
    "additionalProperties": False,
}

PLAN_FIELDS = ("verdict", "limiter", "plan")
REVIEW_FIELDS = ("week", "next")


def _schema(fields: tuple[str, ...]) -> dict:
    return {"type": "object", "properties": {k: {"type": "string"} for k in fields},
            "required": list(fields), "additionalProperties": False}


ACTION_KINDS = ("schedule_run", "move_run", "delete_run", "set_note", "sync", "apply_thresholds")
RUN_KINDS = ("easy", "long", "hills", "tempo", "intervals", "hike", "race", "other")
MAX_ACTIONS = 3


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


ACTION_SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": list(ACTION_KINDS)}, "day": _nullable("string"),
                   "run_kind": {"anyOf": [{"type": "string", "enum": list(RUN_KINDS)}, {"type": "null"}]},
                   "miles": _nullable("number"), "gain_ft": _nullable("number"), "minutes": _nullable("number"),
                   "run_id": _nullable("integer"), "note": _nullable("string")},
    "required": ["kind", "day", "run_kind", "miles", "gain_ft", "minutes", "run_id", "note"],
    "additionalProperties": False,
}
CHAT_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "used": {"type": "array", "items": {"type": "string"}},
                   "actions": {"type": "array", "items": ACTION_SCHEMA}},
    "required": ["answer", "used", "actions"],
    "additionalProperties": False,
}

def _maybe(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


INTAKE_FIELDS = {"name": "string", "date": "string", "distance_mi": "number", "distance_km": "number",
                 "gain_ft": "number", "gain_m": "number", "loss_ft": "number", "loss_m": "number"}
INTAKE_SCHEMA = {"type": "object", "properties": {k: _maybe(t) for k, t in INTAKE_FIELDS.items()},
                 "required": list(INTAKE_FIELDS), "additionalProperties": False}
EDIT_KINDS = ("long_day", "add_day", "drop_day", "weekday_minutes", "weekend_minutes", "week", "workout")
EDIT_SCHEMA = {
    "type": "object",
    "properties": {"edits": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": list(EDIT_KINDS)}, "day": _maybe("integer"),
                       "minutes": _maybe("integer"), "week": _maybe("integer"),
                       "dims": {"type": "array", "items": {"type": "string", "enum": ["miles", "vert"]}},
                       "direction": {"anyOf": [{"type": "string", "enum": ["less", "more"]}, {"type": "null"}]},
                       "workout": {"anyOf": [{"type": "string", "enum": ["none", "track", "tempo", "hill_repeats", "race_pace",
                                                                              "threshold", "fartlek", "progression"]},
                                             {"type": "null"}]}},
        "required": ["kind", "day", "minutes", "week", "dims", "direction", "workout"], "additionalProperties": False}},
        "cannot": _maybe("string")},
    "required": ["edits", "cannot"],
    "additionalProperties": False,
}

# Reading the athlete's own words into fields. These replies hold no prose, so the number check
# doesn't apply; the engine checks every field before using it.
INTAKE_PROMPT = """Read the athlete's text, which describes the race or mountain day they are training for, into fields. Use null for anything the text doesn't say; never guess. Put a distance in kilometers in distance_km and climbing in meters in gain_m or loss_m, unconverted. A "50k" is a distance of 50 km. date is YYYY-MM-DD: when the text gives no year, use the first such date after today (in the data). name is the event or route name as written, without the date or numbers. athlete_note, when present, is the athlete's own training note: when the text points at a race or day the note names (like "my race" or "the 100"), read that one from the note; never fill a field from the note otherwise."""
EDIT_PROMPT = """Read the athlete's request to change their training plan into edits. Days are 0 (Monday) to 6 (Sunday). Kinds: long_day (move the long day to day), add_day or drop_day (a training day), weekday_minutes or weekend_minutes (the most time on one such day, in minutes), week (week number counted from this week as week 1, with dims miles and/or vert and direction less or more), workout (a weekly speed session: track for track or interval work, tempo for tempo runs, threshold for threshold or cruise intervals, fartlek for fartlek, progression for progression runs, hill_repeats for hill repeats or uphill intervals, race_pace for miles at goal race or marathon pace, none to drop speed work). Leave unused fields null and dims empty when not a week edit. Anything else, such as a new objective, a pace or a session on one date, can't be done: return no edits for it and put what was asked in cannot, in a few plain words (like "a long run on Nov 3"); otherwise cannot is null. athlete_note, when present, is the athlete's own training note: use it to read a vague request (like "more of what my note says" or "less of the thing I want to cut"), but never add an edit the request doesn't ask for."""

# Looking up a named race: the analysis model with web search, for the date, distance and vert of
# the next edition. Its numbers are shown for the athlete to confirm before anything uses them.
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 4}
LOOKUP_SYSTEM = "You look up trail races and mountain objectives for a training app. Be exact; never guess."
LOOKUP_PROMPT = """The athlete is training for the race or objective in their text (in the data). Search the web for its official page and find the next edition after today (in the data), or the year they name ("next year" is the year after today's). Find its date, distance and total climbing for the distance they name: "100M" or "100 mi" is the 100-mile race, "50k" the 50 km one. athlete_note, when present, is the athlete's training note: when the text only points at their race ("my race"), search for the one the note names. Convert km to miles and meters to feet. loss_ft is the total descent, or null if the page doesn't give it. If you can't find the race, or the page doesn't give a number, use null for it; never estimate. End your reply with one line holding only this JSON: {"found": true or false, "name": the race's name, "date": "YYYY-MM-DD" or null, "distance_mi": number or null, "gain_ft": number or null, "loss_ft": number or null, "source": the page's URL or null}"""
MAX_CONTINUES = 3


def lookup_race(client: Client, text: str, today: str, model_id: str, note: str = "") -> tuple[dict | None, str | None, dict | None]:
    """A named race's next date, distance and vert from the web. Returns the fields read from the
    model's last JSON line (None when it found nothing usable), the model, and the usage."""
    messages = [{"role": "user", "content": LOOKUP_PROMPT + "\nData:\n" + json.dumps({"today": today, "text": text} | ({"athlete_note": note} if note else {}))}]
    usage, resp = None, None
    for _ in range(MAX_CONTINUES + 1):
        resp = client.beta.messages.create(
            model=model_id, max_tokens=8000, **_fallback_args(model_id),
            system=[{"type": "text", "text": LOOKUP_SYSTEM}], output_config={"effort": "low"},
            tools=[WEB_SEARCH], messages=messages)
        usage = _add_usage(usage, _usage(resp))
        if resp.stop_reason != "pause_turn":
            break
        # The server paused its search loop: send the turn back and it carries on.
        messages = [messages[0], {"role": "assistant", "content": resp.content}]
    if resp is None or resp.stop_reason in ("refusal", "max_tokens", "pause_turn"):
        return None, getattr(resp, "model", None), usage
    body = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    found = None
    for m in re.finditer(r"\{[^{}]*\}", body):
        try:
            found = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    if not isinstance(found, dict) or not found.get("found"):
        return None, resp.model, usage
    return found, resp.model, usage

NOTE_FIELDS = ("note",)
NOTE_MAX = 1500
NOTE_PROMPT = """Write the athlete's training note: the kind of training they want, which the coach will read before every answer. Write it in their voice ("I ..."), as three to six short sentences, from their answers and their recent training. Keep every goal and limit they gave. Add what their history shows that matters for those goals, such as how much they run and climb a week now, how often they rest or how much of their time is below AeT. Do not invent goals, races, dates, injuries or preferences they didn't give. Miles and feet. Plain text, no markdown."""


def note_packet(answers: dict, acts: list[dict], day: date) -> dict:
    """Context for drafting the training note: the athlete's answers and their recent training."""
    return {"date": day.isoformat(), "answers": {k: v for k, v in answers.items() if v},
            "weeks": weekly_history(acts, day, 8), "bests_90d": bests(acts, day), "rest": rest_pattern(acts, day)}


def rules_note(packet: dict) -> dict:
    """The note from the answers alone, for when the coach is off or fails."""
    a = packet["answers"]
    lines = [f"{label} {a[k].strip().rstrip('.')}." for k, label in (
        ("training_for", "I'm training for"), ("wants", "I want"), ("avoid", "Avoid or limits:")) if a.get(k)]
    return {"note": " ".join(lines)}


LOAD_FIELDS = ("last_week", "trend", "watch", "next")


def load_packet(today: dict, acts: list[dict], next_week: dict) -> dict:
    """Context for the Load page's read, built from the /api/today payload, the athlete's
    activities and the Load page's next-7-days block."""
    day = date.fromisoformat(today["date"])
    sched = next_week["scheduled"]
    return {
        "date": today["date"],
        "readiness": {k: {"state": today["readiness"][k]["state"], "reason": today["readiness"][k]["reason"]}
                      for k in ("engine", "legs")},
        "overnight": _overnight(today.get("wellness")),
        "ledgers": {name: _ledger(d, name) for name, d in today["ledgers"].items()},
        "weeks": weekly_history(acts, day, 8),
        "rest": rest_pattern(acts, day),
        "intensity_28d_pct": _intensity(today),
        "next_7_days": {"start": next_week["start"], "end": next_week["end"],
                        "scheduled": {"runs": sched["runs"], "distance_mi": _r(sched["miles"], 1),
                                      "gain_ft": _r(sched["gain_ft"]), "loss_ft": _r(sched["loss_ft"]),
                                      "hours": _r(sched["hours"], 1)},
                        "room": {name: {"unit": r["unit"], "hold": _r(r["hold"], 1 if r["unit"] in ("hours", "miles") else 0),
                                        "limit": _r(r["limit"], 1 if r["unit"] in ("hours", "miles") else 0)}
                                 for name, r in next_week["room"].items()}},
    }


def rules_load(packet: dict) -> dict:
    """The Load read from engine numbers alone."""
    weeks = [w for w in packet["weeks"] if w["complete"]]
    last = weeks[-1] if weeks else None
    last_week = ("No complete week yet." if last is None else
                 f"{last['distance_mi']} mi with {last['gain_ft']:,} ft up and {last['loss_ft']:,} ft down"
                 f" in {last['hours']} h.")
    led = packet["ledgers"]
    moving = {k: v["vs_normal_pct"] for k, v in led.items() if v["vs_normal_pct"] is not None}
    if moving:
        up = max(moving, key=lambda k: moving[k])
        trend = f"{up.capitalize()} is furthest above its normal: {moving[up]}%." if moving[up] > 0 else \
            "Every load sits at or under its normal."
    else:
        trend = "Your normal is still building."
    flagged = [f"{k.capitalize()}: {f[0].lower()}{f[1:]}" for k, v in led.items() for f in v["flags"] if f]
    watch = ("; ".join(flagged) + ".") if flagged else "Nothing stands out."
    room = packet["next_7_days"]["room"]
    if "distance" in room and "climb" in room:
        nxt = (f"Holding your normal over the next 7 days is about {room['distance']['hold']} mi and "
               f"{room['climb']['hold']:,} ft up; ramping starts past {room['distance']['limit']} mi or "
               f"{room['climb']['limit']:,} ft up.")
    else:
        nxt = "Not enough history yet to size the next 7 days."
    return {"last_week": last_week, "trend": trend, "watch": watch, "next": nxt}


NAME_PROMPT = """Name this activity the way a runner would title it: two to six words, leading with the peak or place when the data has one, then the route or the kind of day, for example "Mt Baldy via the Bowl", "Easy loop at Griffith Park", "Hill repeats on Mt Wilson Trail", "Long steep hike". Use only place names that appear in the data, never invent one; name_from_device often holds the town. Pick the kind of day from the numbers: a big climb, a long day, an easy run, a hike when most of it was hiking, hard when much of it was above AnT. No numbers, dates, emoji, quotes or markdown."""

BUILD_FOCUS = ("build", "peak", "hold", "recovery", "taper", "race")
BUILD_WORKOUTS = ("none", "track", "tempo", "threshold", "fartlek", "progression", "hill_repeats", "race_pace")
BUILD_HILLS = ("vert_climb", "power_hike", "downhill", "muscular_endurance", "hill_sprints")
BUILD_SCHEMA = {
    "type": "object",
    "properties": {
        "approach": {"type": "string"},
        "weeks": {"type": "array", "items": {
            "type": "object",
            "properties": {"start": {"type": "string"}, "focus": {"type": "string", "enum": list(BUILD_FOCUS)},
                           "miles": {"type": "number"}, "gain_ft": {"type": "number"}, "loss_ft": {"type": "number"},
                           "workout": {"type": "string", "enum": list(BUILD_WORKOUTS)},
                           "hill_session": {"type": "string", "enum": list(BUILD_HILLS)}, "note": {"type": "string"}},
            "required": ["start", "focus", "miles", "gain_ft", "loss_ft", "workout", "hill_session", "note"],
            "additionalProperties": False}},
    },
    "required": ["approach", "weeks"], "additionalProperties": False,
}
BUILD_PROMPT = """Build this athlete's training plan, week by week, as their coach. The data holds the objective or goal (objective.event false means a block with no race day: no taper and no race week), the athlete's training days and time limits, their last 12 weeks and biggest days, athlete_note and athlete_answers when they gave them, and engine_draft: the engine's own plan, one entry per week, as a safe reference. weekly_targets_the_objective_needs is the weekly volume the engine sizes the peak weeks to; readiness_now and engine_draft_readiness_at_taper show each readiness dimension against what the objective asks, now and at the taper on the engine's draft: shape the weeks so the plan closes the dimensions still short (the limiter first), within the limits. A second coach checks your plan before the athlete sees it. Write exactly one entry per engine_draft week, in the same order with the same start. For each week choose the focus, the week's miles, gain_ft and loss_ft, the one speed session (or none), the hill day's session, and a note of one short sentence for the athlete on the week's purpose and its key sessions. Speed sessions: track (800 m repeats over AnT), threshold (6 min repeats at AnT), tempo (blocks just under AnT), fartlek (mixed surges on rolling trail), progression (easy building to tempo), hill_repeats (2 min hard uphill), race_pace (miles at goal pace). Hill day sessions: vert_climb (a long zone 2 climb, also the easy option for recovery and taper weeks), power_hike (6 min steep power-hike intervals), downhill (downhill repeats for quad durability), muscular_endurance (weighted-pack hiking on the steepest climb, best in the last build and peak weeks), hill_sprints (10 to 15 s uphill sprints, for road races). Vary them through the build so the athlete trains climbing, hiking, descending and leg strength; the engine also adds strides, recovery runs after long days and two strength sessions a week on its own. Shape the plan the way a good mountain coach would for this athlete and this goal: how fast to build, where the recovery weeks fall, when to peak, how long to taper, when to lean on vert or on miles, and which speed work fits (none for most ultra and mountain builds; track or tempo for road races and getting faster; hill repeats for steep objectives). Respect the note and answers: an injury or niggle, tired lately, little vert near home, a preference they state. The engine checks every week and holds anything that breaks its limits: no week may add more than 10% in any of miles, gain or loss over the last build week, and a week that would ramp a load too fast or overreach is cut. So plan within those limits; a week you ask for above them is cut back. Recovery weeks run about 60 to 75% of the build level. For an event, the last week is the race week (focus race) and the weeks before it taper. Miles and feet only. approach is two or three sentences on how you shaped the plan and why, for the athlete."""

# A second read of the plan before the athlete sees it: the coach checks the weeks the engine
# held to its limits and, when something is off, sends back a revised plan the engine checks again.
CHECK_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "summary": {"type": "string"},
                   "issues": {"type": "array", "items": {"type": "string"}},
                   "weeks": BUILD_SCHEMA["properties"]["weeks"]},
    "required": ["ok", "summary", "issues", "weeks"], "additionalProperties": False,
}
CHECK_MAX_ISSUES = 5
CHECK_PROMPT = """Check this training plan as a second coach, before the athlete sees it. plan_to_check is the plan as it stands after the engine held every week to its limits: each week's focus, miles, feet up and down, hours, long day, speed session, hill session, the note to the athlete (coach_note) and the engine's own notes (a week held to the 10% build limit, or cut for a load limit). coach_approach is how the builder says it shaped the plan. Check it against the objective, readiness now and projected at the taper (which dimension is still a gap, and does the plan train it), the athlete's training days, time limits, note and answers, and their last 12 weeks and biggest days. Look for: a week or long day their history doesn't support; a build too fast or too timid for the time there is; a missing or misplaced recovery week; a taper too short or too long for the distance; a gap dimension (climbing volume, descent durability, time on feet, the big day) the weeks don't close; speed work that doesn't fit the goal or the note; hill sessions that never vary or don't suit the week; a week the engine cut that leaves the shape odd, such as a jump right after it; a coach_note that no longer matches its week; an injury, tiredness or preference the plan ignores. ok is true when the plan is sound as it is: then weeks is empty and issues holds at most minor points. When something should change, ok is false, issues names each problem in one short sentence (at most five), and weeks is the whole revised plan: exactly one entry per engine_draft week, in the same order with the same start, in the builder's form (focus, miles, gain_ft, loss_ft, workout, hill_session, note), each week still inside the engine's limits (no more than 10% over the last build week in any of miles, gain or loss; recovery weeks about 60 to 75% of the build level). Change only what the issues need and keep the rest of the plan as it is. summary is one or two sentences for the athlete on what the check found and, when ok is false, what it changed. Miles and feet only."""

# After each sync with a plan accepted: does the latest training help the plan?
FIT_FIELDS = ("helps", "week", "next")
FIT_DAYS = 3  # today and the two days before it
FIT_PROMPT = """Plan fit: the athlete just synced. Say whether their latest training helps their plan. recent_days holds each of the last few days: the plan's session for it (planned: kind, title, size and benefit, what the session is for), or null for a rest day, and what they did (done: each activity, matched to the day by the calendar), or empty. unplanned holds activities with no planned session behind them. this_week is the week's target with what's done and what's still scheduled; flags are load flags raised today; replan_suggested lists reasons the engine suggests a new plan. Three fields, one or two sentences each:
- helps: did the latest training do the planned session's job and move the athlete toward the objective? Set its miles, feet up and down and time against the planned ones, and judge it against the session's benefit (an easy day run hard, a long day cut short or a hill day with little vert doesn't do its job). For a missed or swapped session, or an unplanned run, say what it gave or cost the plan.
- week: where this week stands against its target, counting what's still scheduled.
- next: what to do with the rest of the week: carry on as planned, or ease off when a load is flagged or a run went well over. Never tell them to make up missed miles. Name the next planned session. When replan_suggested is not empty, say a new plan is suggested on the Plan page and why."""

# Reads that run on the brief's cheaper model: short, once a day or once an activity.
CHEAP_MODES = {"load", "name", "fit"}

MODES = {
    # Short reply, so low effort keeps it quick; analysis gets more thought.
    "brief": {"label": "daily brief", "schema": BRIEF_SCHEMA, "effort": "low", "max_tokens": 6000},
    "analysis": {"label": "workout analysis", "schema": ANALYSIS_SCHEMA, "effort": "medium", "max_tokens": 12000},
    "plan": {"label": "plan", "schema": _schema(PLAN_FIELDS), "effort": "medium", "max_tokens": 16000},
    "review": {"label": "weekly review", "schema": _schema(REVIEW_FIELDS), "effort": "low", "max_tokens": 8000},
    "chat": {"label": "chat", "schema": CHAT_SCHEMA, "effort": "low", "max_tokens": 8000},
    # Plan setup in conversation: reading free text into fields, on the cheaper model.
    "intake": {"label": "plan intake", "schema": INTAKE_SCHEMA, "effort": "low", "max_tokens": 2000,
               "prompt": INTAKE_PROMPT, "fields_only": True},
    "edit": {"label": "plan change", "schema": EDIT_SCHEMA, "effort": "low", "max_tokens": 2000,
             "prompt": EDIT_PROMPT, "fields_only": True},
    "load": {"label": "load read", "schema": _schema(LOAD_FIELDS), "effort": "low", "max_tokens": 6000},
    "name": {"label": "activity name", "schema": _schema(("name",)), "effort": "low", "max_tokens": 1000,
             "prompt": NAME_PROMPT},
    # Building the plan's weeks; the engine checks them. Its numbers are new by design.
    "build": {"label": "plan builder", "schema": BUILD_SCHEMA, "effort": "medium", "max_tokens": 20000,
              "prompt": BUILD_PROMPT, "free_numbers": True},
    # Checking the built plan and revising it when needed; the engine checks any revision again.
    "check": {"label": "plan check", "schema": CHECK_SCHEMA, "effort": "medium", "max_tokens": 20000,
              "prompt": CHECK_PROMPT, "free_numbers": True},
    "fit": {"label": "plan fit", "schema": _schema(FIT_FIELDS), "effort": "low", "max_tokens": 6000,
            "prompt": FIT_PROMPT},
    # Drafting the athlete's training note from a few answers and their history.
    "note": {"label": "training note", "schema": _schema(NOTE_FIELDS), "effort": "low", "max_tokens": 4000,
             "prompt": NOTE_PROMPT},
}
FIELD_MODES = {k for k, v in MODES.items() if v.get("fields_only")}


# Context packets ---------------------------------------------------------------------------

def _r(v: float | None, nd: int = 0) -> float | int | None:
    if v is None:
        return None
    return round(v) if nd == 0 else round(v, nd)


def _hm(seconds: float) -> str:
    m = int(round(seconds / 60))
    return f"{m // 60}:{m % 60:02d}"


def _zones(z: dict | None) -> dict | None:
    return None if z is None else {k: _r(v) for k, v in z.items()}


ENGINE_SPLIT = ("below_aet", "aet_to_ant", "above_ant")


def _split(z: dict | None) -> dict | None:
    """The engine's own intensity split (zone_minutes z1-z3), named for what it is."""
    return None if z is None else {name: _r(z[f"z{k + 1}"]) for k, name in enumerate(ENGINE_SPLIT)}


def _zone_text(zone_model: dict | None) -> str:
    if zone_model is None:
        return "zone 1 below AeT, zone 2 AeT to AnT, zone 3 above AnT"
    return ", ".join(f"zone {z['zone']} {z['text']} ({z['name'].lower()})" for z in zone_model["list"])


def _athlete(th: dict, zone_model: dict | None = None) -> dict:
    """zones describes the zone model the athlete chose in Settings; zone_minutes use it."""
    return {"hr_resting": _r(th["resting"]), "hr_aet": _r(th["aet"]), "hr_ant": _r(th["ant"]),
            "hr_max": _r(th["max"]), "zones": _zone_text(zone_model)}


def _nd(name: str) -> int:
    """Miles to a tenth, other loads to the whole number."""
    return 1 if name == "distance" else 0


def _ledger(d: dict, name: str = "") -> dict:
    nd = _nd(name)
    return {"vs_normal_pct": None if d["vs_normal"] is None else _r(d["vs_normal"] * 100),
            "last_7_days_per_day": _r(d["fatigue"], nd), "normal_per_day": _r(d["fitness"], nd),
            "load_today": _r(d["load"], nd), "flags": [f["text"] for f in d["flags"]]}


def _activity_line(a: dict) -> dict:
    return {"day": a["day"], "sport": a["sport"], "distance_mi": _r(a["distance_mi"], 1),
            "gain_ft": _r(a["gain_ft"]), "loss_ft": _r(a["loss_ft"]), "effort_mi": _r(a["effort_mi"], 1),
            "moving_time": _hm(a["moving_time_s"]), "zone_minutes": _zones(a.get("hr_zone_minutes", a["zone_minutes"])),
            "cardio_load": _r(a["cardio_load"]), "climb_load": _r(a["climb_load"]),
            "descent_load": _r(a["descent_load"])}


def _overnight(w: dict | None) -> dict | None:
    if w is None:
        return None
    hrv, rhr, sleep = w["hrv"], w["resting_hr"], w["sleep"]
    return {
        "hrv_ms": {"status": hrv["status"], "last_night": _r(hrv["last_night"]), "avg_7d": _r(hrv["avg_7d"]),
                   "baseline_60d": _r(hrv["baseline"]),
                   "vs_baseline_pct": None if hrv["vs_baseline"] is None else _r(hrv["vs_baseline"] * 100)},
        "resting_hr_bpm": {"status": rhr["status"], "last_night": _r(rhr["last_night"]),
                           "baseline_60d": _r(rhr["baseline"]), "vs_baseline": _r(rhr["vs_baseline_bpm"])},
        "sleep_hours": {"status": sleep["status"], "last_night": _r(sleep["last_night_h"], 1),
                        "avg_7d": _r(sleep["avg_7d_h"], 1), "need": _r(sleep["need_h"], 1)},
    }


def _score(s: dict | None) -> dict | None:
    if not s:
        return None
    return {"score": s["score"], "label": s["label"], "held": s["held"],
            "points_lost": [{"input": i["name"], "points": -i["points"], "why": i["text"]}
                            for i in s["items"] if i["points"] < 0],
            "not_used": s["missing"]}


def _hours(seconds: float) -> float | None:
    return _r(seconds / 3600, 1)


def _below_aet_pct(acts: list[dict]) -> int | None:
    zs = [a["zone_minutes"] for a in acts if a.get("zone_minutes")]
    total = sum(sum(z.values()) for z in zs)
    return None if not total else _r(sum(z["z1"] for z in zs) / total * 100)


def _day_line(a: dict) -> dict:
    return {"day": a["day"], "sport": a["sport"], "distance_mi": _r(a["distance_mi"], 1),
            "gain_ft": _r(a["gain_ft"]), "loss_ft": _r(a["loss_ft"]), "moving_time": _hm(a["moving_time_s"])}


def weekly_history(acts: list[dict], day: date, weeks: int) -> list[dict]:
    """Totals for each of the last `weeks` Monday-to-Sunday weeks, oldest first, this week last."""
    monday = day - timedelta(days=day.weekday())
    out = []
    for k in range(weeks - 1, -1, -1):
        start = monday - timedelta(weeks=k)
        end = start + timedelta(days=6)
        wk = [a for a in acts if start.isoformat() <= a["day"] <= end.isoformat()]
        longest = max(wk, key=lambda a: a["distance_mi"], default=None)
        out.append({"week_start": start.isoformat(), "complete": end < day, "activities": len(wk),
                    "days_active": len({a["day"] for a in wk}),
                    "distance_mi": _r(sum(a["distance_mi"] for a in wk), 1),
                    "gain_ft": _r(sum(a["gain_ft"] for a in wk)), "loss_ft": _r(sum(a["loss_ft"] for a in wk)),
                    "hours": _hours(sum(a["moving_time_s"] for a in wk)), "below_aet_pct": _below_aet_pct(wk),
                    "longest": None if longest is None else _day_line(longest)})
    return out


def bests(acts: list[dict], day: date, days: int = BEST_DAYS) -> dict | None:
    """The athlete's biggest single activities in the window, by distance, climb and descent."""
    since = (day - timedelta(days=days - 1)).isoformat()
    recent = [a for a in acts if since <= a["day"] <= day.isoformat()]
    if not recent:
        return None
    return {key: _day_line(max(recent, key=lambda a: a[field]))
            for key, field in (("longest", "distance_mi"), ("most_gain", "gain_ft"), ("most_loss", "loss_ft"))}


def rest_pattern(acts: list[dict], day: date) -> dict:
    """Training days in a row up to yesterday, and rest days in the 14 days before today."""
    active = {a["day"] for a in acts}
    streak = 0
    d = day - timedelta(days=1)
    while d.isoformat() in active and streak < 60:
        streak += 1
        d -= timedelta(days=1)
    rest = sum(1 for k in range(1, 15) if (day - timedelta(days=k)).isoformat() not in active)
    return {"training_days_in_a_row_before_today": streak, "rest_days_last_14": rest,
            "trained_today": day.isoformat() in active}


def _efficiency(a: dict, cardio: float | None) -> dict:
    """Engine-computed rates, so the coach can compare activities without doing math."""
    effort, hours = a["effort_mi"] or 0, a["moving_time_s"] / 3600
    return {"min_per_effort_mi": _r(a["moving_time_s"] / 60 / effort, 1) if effort > 0.5 else None,
            "cardio_load_per_hour": _r(cardio / hours) if cardio is not None and hours > 0.2 else None}


def similar_recent(a: dict, acts: list[dict], n: int = SIMILAR_MAX) -> list[dict]:
    """Earlier activities of the same sport closest in effort miles, from the last SIMILAR_DAYS."""
    day = date.fromisoformat(a["day"])
    since = (day - timedelta(days=SIMILAR_DAYS)).isoformat()
    effort = a["effort_mi"] or 0
    pool = [b for b in acts if b["id"] != a["id"] and b["sport"] == a["sport"] and since <= b["day"]
            and b["start_time"] < a["start_time"] and effort and 0.67 <= (b["effort_mi"] or 0) / effort <= 1.5]
    pool.sort(key=lambda b: abs(b["effort_mi"] - effort))
    return [_day_line(b) | {"effort_mi": _r(b["effort_mi"], 1), "below_aet_pct": _below_aet_pct([b]),
                            "cardio_load": _r(b["cardio_load"]), "climb_load": _r(b["climb_load"]),
                            "descent_load": _r(b["descent_load"])} | _efficiency(b, b["cardio_load"])
            for b in sorted(pool[:n], key=lambda b: b["start_time"])]


def biggest_in(a: dict, acts: list[dict], days: int = BEST_DAYS) -> list[str]:
    """The dimensions where this activity was the biggest of its sport in the window before it."""
    day = date.fromisoformat(a["day"])
    since = (day - timedelta(days=days - 1)).isoformat()
    pool = [b for b in acts if b["sport"] == a["sport"] and since <= b["day"] <= a["day"]]
    if len(pool) < 3:
        return []
    return [name for name, f in (("distance", "distance_mi"), ("climb", "gain_ft"), ("descent", "loss_ft"),
                                 ("moving time", "moving_time_s"))
            if a[f] > 0 and a[f] >= max(b[f] for b in pool)]


def _week_so_far(week: dict) -> dict:
    out = {"start": week["start"], "activities": len(week["activities"]),
           "distance_mi": _r(week["distance_mi"], 1), "gain_ft": _r(week["gain_ft"]),
           "loss_ft": _r(week["loss_ft"])}
    if last := week.get("last_week_to_date"):
        out["same_days_last_week"] = {"activities": last["activities"], "distance_mi": _r(last["distance_mi"], 1),
                                      "gain_ft": _r(last["gain_ft"]), "loss_ft": _r(last["loss_ft"])}
    return out


def _vol(v: dict | None) -> dict | None:
    """A calendar volume (miles, gain_ft, loss_ft) in the packet's names."""
    return None if v is None else {"distance_mi": _r(v["miles"], 1), "gain_ft": _r(v["gain_ft"]),
                                   "loss_ft": _r(v["loss_ft"])}


def _brief_plan(today: dict) -> dict | None:
    """What the brief knows of the plan: the objective (without its name, the athlete's text), today's
    session and what it is for, this week against its target, and the next few days. None when the
    athlete has no plan and nothing scheduled."""
    ahead = today.get("ahead")
    if not ahead:
        return None
    planned = today.get("planned")
    days = [{"day": d["day"], "weekday": date.fromisoformat(d["day"]).strftime("%A"),
             "planned": [_planned(r) for r in d["runs"]] or None} for d in ahead["days"]]
    if not ahead["has_plan"] and not planned and not any(d["planned"] for d in days):
        return None
    o, w = ahead.get("objective"), ahead.get("week")
    return {
        "objective": None if o is None else {"goal": o["goal_text"], "event": o["event"], "date": o["day"],
                                             **({"days_to_go": o["days_to_go"]} if o["event"] else {"block_ends": o["day"]})},
        "today_planned": None if not planned else _planned(planned),
        "this_week": None if w is None else {"focus": (w.get("target") or {}).get("focus"), "target": _vol(w.get("target")),
                                             "done": _vol(w.get("done")), "still_scheduled": _vol(w.get("ahead"))},
        "next_days": days,
    }


def _score_days(s: dict | None) -> list[dict] | None:
    trend = (s or {}).get("trend")
    return None if not trend else [{"day": d["day"], "score": d["score"]} for d in trend[-7:]]


def _intensity(today: dict) -> dict:
    return {name: _r(today["intensity_28d"]["share"][f"z{k + 1}"] * 100) for k, name in enumerate(ENGINE_SPLIT)}


def brief_packet(today: dict, last_14_days: list[dict], history: list[dict] | None = None) -> dict:
    """Compact context for the daily brief, built from the /api/today payload. history is the
    athlete's activities for the weekly totals; without it the brief reads the last 14 days only."""
    ready = today["readiness"]
    day = date.fromisoformat(today["date"])
    acts = last_14_days if history is None else history
    return {
        "date": today["date"],
        "weekday": day.strftime("%A"),
        "athlete": _athlete(today["thresholds"], today.get("zone_model")),
        "checkin": today["checkin"],
        "overnight": _overnight(today.get("wellness")),
        "readiness": {"engine": {k: ready["engine"][k] for k in ("state", "reason")},
                      "legs": {k: ready["legs"][k] for k in ("state", "reason", "limiter")}},
        "readiness_score": _score(today.get("score")),
        "readiness_score_last_7_days": _score_days(today.get("score")),
        "rule_call": {k: today["call"][k] for k in ("call", "session", "detail", "why")},
        "plan": _brief_plan(today),
        "ledgers": {name: _ledger(d, name) for name, d in today["ledgers"].items()},
        "week_so_far": _week_so_far(today["week"]),
        "intensity_28d_pct": _intensity(today),
        "last_14_days": [_activity_line(a) for a in sorted(last_14_days, key=lambda a: a["start_time"])],
        "weeks": weekly_history(acts, day, BRIEF_WEEKS),
        "rest": rest_pattern(acts, day),
    }


def segment_labels(segments: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    out = []
    for s in segments:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
        out.append(f"{s['kind'].capitalize()} {counts[s['kind']]}")
    return out


def analysis_packet(a: dict, thresholds: dict, ledgers_now: dict, today: date,
                    history: list[dict] | None = None) -> dict:
    """Compact context for a workout analysis, built from the /api/activities/{id} payload.
    Streams and GPS are left out; segments carry the shape of the effort. history is the athlete's
    activity list, for comparing this one with similar earlier ones."""
    history = history or []
    zm = a.get("hr_zone_minutes", a["zone_minutes"])
    total_z = sum(zm.values()) if zm else 0
    segs = a["segments"][:MAX_SEGMENTS]
    return {
        "athlete": _athlete(thresholds, a.get("zone_model")),
        "activity": {
            "day": a["day"], "days_ago": (today - date.fromisoformat(a["day"])).days, "sport": a["sport"],
            "distance_mi": _r(a["distance_mi"], 1), "gain_ft": _r(a["gain_ft"]), "loss_ft": _r(a["loss_ft"]),
            "effort_mi": _r(a["effort_mi"], 1), "moving_time": _hm(a["moving_time_s"]),
            "elapsed_time": _hm(a["elapsed_time_s"]), "hike_share_pct": _r(a["hike_share"] * 100),
            "zone_minutes": _zones(zm),
            "zone_pct": None if not total_z else {k: _r(v / total_z * 100) for k, v in zm.items()},
            "intensity_minutes": _split(a["zone_minutes"]) if total_z else None,
            "gain_by_grade_ft": {k: _r(v) for k, v in a["gain_by_band_ft"].items()},
            "loss_by_grade_ft": {k: _r(v) for k, v in a["loss_by_band_ft"].items()},
            "longest_climb_ft": _r(a["longest_climb_ft"]), "longest_descent_ft": _r(a["longest_descent_ft"]),
            "minutes_above_8000ft": _r(a["minutes_above_8000ft"]),
            "cardio_estimated_without_hr": a["cardio_estimated"], "notes": a["notes"],
            "below_aet_pct": _below_aet_pct([a]), **_efficiency(a, a["loads"]["cardio"]["load"]),
        },
        "similar_recent": similar_recent(a, history),
        "biggest_in_90_days": biggest_in(a, history),
        "loads": {name: {"load": _r(l["load"], _nd(name)), "times_usual_day": _r(l["times_usual_day"], 1),
                         "six_week_high": l["six_week_high"]} for name, l in a["loads"].items()},
        "segments": [{"label": label, "distance_mi": _r(s["distance_mi"], 2), "gain_ft": _r(s["gain_ft"]),
                      "loss_ft": _r(s["loss_ft"]), "avg_grade_pct": _r(s["avg_grade"], 1),
                      "time": _hm(s["time_s"]), "hike_share_pct": _r(s["hike_share"] * 100),
                      "zone_minutes": _zones(s.get("hr_zone_minutes", s["zone_minutes"])),
                      "above_ant_min": None if s["zone_minutes"] is None else _r(s["zone_minutes"]["z3"]),
                      "climb_load": _r(s["climb_load"]),
                      "descent_load": _r(s["descent_load"])}
                     for label, s in zip(segment_labels(segs), segs)],
        "ledgers_now": {name: _ledger(d, name) for name, d in ledgers_now.items()},
        **terrain_packet(a.get("terrain")),
    }


def _pace_text(min_per_mi: float | None) -> str | None:
    if not min_per_mi:
        return None
    m = int(round(min_per_mi * 60))
    return f"{m // 60}:{m % 60:02d}/mi"


def terrain_packet(t: dict | None) -> dict:
    """The activity's climbing and descending by grade against the athlete's usual on the same
    grades, hiking against running on the climbs, and how the second half held up."""
    if not t:
        return {}
    grades = []
    for b in t["bands"]:
        u = b["usual"]
        grades.append({
            "grade": f"{b['kind']} {b['label']}", "vert_ft": b["vert_ft"], "time": _hm(b["time_s"]),
            "vert_rate_ft_per_hour": b["vert_rate_ft_h"],
            "pace": _pace_text(b["pace_min_mi"]) if b["kind"] == "descent" else None,
            "hike_share_pct": _r(b["hike_share"] * 100), "avg_hr": _r(b["avg_hr"]),
            "usual_90d": None if not u else {
                "earlier_activities": u["runs"], "vert_rate_ft_per_hour": u["vert_rate_ft_h"], "avg_hr": u["avg_hr"],
                "rate_vs_usual_pct": u["rate_change_pct"], "hr_vs_usual_bpm": u["hr_change_bpm"],
                "fastest_in_90_days": u["fastest"]}})
    d = t["durability"]
    out = {"grades": grades, "hike_vs_run_on_climbs": [
        {"grade": h["label"], "hiking_ft_per_hour": h["hike_rate_ft_h"], "running_ft_per_hour": h["run_rate_ft_h"],
         "running_faster_pct": h["run_rate_gain_pct"], "hiking_hr": _r(h["hike_hr"]), "running_hr": _r(h["run_hr"]),
         "running_costs_bpm": h["run_hr_cost_bpm"]} for h in t["hike_or_run"]]}
    if d:
        half = lambda h: {"distance_mi": h["distance_mi"], "gain_ft": h["gain_ft"], "time": _hm(h["time_s"]),
                          "grade_adjusted_pace": _pace_text(h["flat_pace_min_mi"]),
                          "steep_climb_ft_per_hour": h["climb_rate_ft_h"], "avg_hr": _r(h["avg_hr"])}
        out["durability"] = {
            "first_half": half(d["first"]), "second_half": half(d["second"]),
            "grade_adjusted_pace": _pace_text(d["flat_pace_min_mi"]),
            "grade_adjusted_pace_change_pct": d["pace_change_pct"],
            "steep_climb_rate_change_pct": d["climb_rate_change_pct"],
            "decoupling_pct": d["decoupling_pct"], "usual_decoupling_pct": d["usual_decoupling_pct"],
            "verdict": d["verdict"]}
    return out


def _week(w: dict) -> dict:
    long = w.get("long")
    return {"start": w["start"], "focus": w["focus"], "distance_mi": _r(w["miles"], 1), "gain_ft": _r(w["gain_ft"]),
            "loss_ft": _r(w["loss_ft"]), "hours": _r(w["hours"], 1), "below_aet_hours": _r(w["below_aet_hours"], 1),
            "long_day": None if not long else {
                "distance_mi": _r(long["miles"], 1), "gain_ft": _r(long["gain_ft"]), "loss_ft": _r(long["loss_ft"]),
                "hours": _r(long["minutes"] / 60, 1), "effort_mi": _r(long["effort_mi"], 1),
                "longest_descent_ft": _r(long.get("longest_descent_ft")),
                "back_to_back_second_day": None if not long.get("second") else {
                    "distance_mi": _r(long["second"]["miles"], 1), "gain_ft": _r(long["second"]["gain_ft"]),
                    "loss_ft": _r(long["second"]["loss_ft"]), "hours": _r(long["second"]["minutes"] / 60, 1)}},
            "held_back_by_load_limit": w["limited_by"], "notes": w["notes"]}


def _dims(ev: dict) -> list[dict]:
    """Objective readiness, one line per dimension: demand, capacity, and capacity as a percent of
    demand against the rule's percent."""
    out = []
    for d in ev["dims"]:
        nd = 1 if d["unit"] in ("h", "effort mi") else 0
        out.append({"dimension": d["name"], "status": d["status"], "demand": _r(d["demand"], nd),
                    "capacity": _r(d["capacity"], nd), "unit": d["unit"],
                    "capacity_pct_of_demand": None if d["ratio"] is None else _r(d["ratio"] * 100),
                    "rule_pct": None if d["rule"] is None else _r(d["rule"] * 100)})
    return out


def _dim_name(ev: dict, key: str | None) -> str | None:
    return next((d["name"] for d in ev["dims"] if d["key"] == key), None)


def _objective(plan: dict, today: date) -> dict:
    obj, dem = plan["objective"], plan["demand"]
    goal = obj.get("goal", "race")
    if goal not in ("race", "trip"):
        # A block with no event: its goal and weekly targets (a yearly target's totals and what's done).
        from .plan import GOAL_KINDS
        out = {"goal": GOAL_KINDS.get(goal, goal), "event": False, "block_ends": obj["day"],
               "weekly_targets": {"distance_mi": _r(plan["peak"]["miles"], 1), "gain_ft": _r(plan["peak"]["gain_ft"]),
                                  "loss_ft": _r(plan["peak"]["loss_ft"])}}
        if t := plan.get("target"):
            out["year_totals_target"] = {"distance_mi": _r(t["totals"]["miles"]), "gain_ft": _r(t["totals"]["gain_ft"])}
            out["year_done_so_far"] = {"distance_mi": _r(t["done"]["miles"]), "gain_ft": _r(t["done"]["gain_ft"])}
        return out
    return {"goal": "Race" if goal == "race" else "Mountain objective", "event": True,"date": obj["day"], "days_to_go": (date.fromisoformat(obj["day"]) - today).days,
            "distance_mi": _r(obj["distance_mi"], 1), "gain_ft": _r(obj["gain_ft"]), "loss_ft": _r(obj["loss_ft"]),
            "effort_mi": _r(dem["effort_mi"], 1), "longest_descent_ft": _r(obj["longest_descent_ft"]),
            "high_point_ft": _r(obj["high_point_ft"]),
            "estimated_hours": {"low": _r(dem["time"]["low_h"], 1), "high": _r(dem["time"]["high_h"], 1)}}


def plan_packet(plan: dict, now: dict, today: date, reason: str, changes: list[dict]) -> dict:
    """Context for explaining a plan. The objective's name is left out: it is the athlete's text."""
    opts = plan.get("options") or {}
    smaller = opts.get("smaller")
    proj = plan.get("projected")
    return {
        "objective": _objective(plan, today),
        "readiness_now": _dims(now) if now else [], "limiter_now": _dim_name(now, now["limiter"]) if now else None,
        "readiness_projected_at_taper": _dims(proj) if proj else [],
        "limiter_projected": _dim_name(proj, proj["limiter"]) if proj else None,
        "reachable": plan["reachable"],
        "options": {"smaller_version": None if not smaller else {
                        "distance_mi": _r(smaller["distance_mi"], 1), "gain_ft": _r(smaller["gain_ft"], -2),
                        "loss_ft": _r(smaller["loss_ft"], -2)},
                    "later_date": opts.get("later")},
        "recent_per_week": {"distance_mi": _r(plan["baseline"]["miles"], 1), "gain_ft": _r(plan["baseline"]["gain_ft"]),
                            "loss_ft": _r(plan["baseline"]["loss_ft"]), "hours": _r(plan["baseline"]["hours"], 1)},
        "rules": {"build_week_max_increase_pct": 10, "recovery": "a recovery week after every three build weeks"},
        "reason_for_this_version": reason,
        "changes_from_accepted_plan": [{"week": c["start"], "before": _brief_week(c["before"]),
                                        "after": _brief_week(c["after"])} for c in changes],
        "weeks": [_week(w) for w in plan["weeks"]],
    }


def build_packet(plan: dict, acts: list[dict], today: date) -> dict:
    """Context for building a plan: the goal, the athlete's limits and history, and the engine's
    draft of the same weeks as a reference."""
    from .plan import BUILD_RATE, DAY_NAMES

    c = plan["constraints"]
    return {
        "objective": _objective(plan, today),
        "race_type": plan.get("race_type"),
        "training_days": [DAY_NAMES[d] for d in c["days"]], "long_day": DAY_NAMES[c["long_day"]],
        "weekday_max_minutes": c["weekday_min"], "weekend_max_minutes": c["weekend_min"],
        "speed_work_asked_for": c.get("workout") if c.get("workout") not in (None, "none") else None,
        "back_to_back_long_days": plan.get("back_to_back"),
        "recent_per_week": {"distance_mi": _r(plan["baseline"]["miles"], 1), "gain_ft": _r(plan["baseline"]["gain_ft"]),
                            "loss_ft": _r(plan["baseline"]["loss_ft"]), "hours": _r(plan["baseline"]["hours"], 1)},
        "weekly_targets_the_objective_needs": {"distance_mi": _r(plan["peak"]["miles"], 1),
                                               "gain_ft": _r(plan["peak"]["gain_ft"]), "loss_ft": _r(plan["peak"]["loss_ft"])},
        "readiness_now": _dims(now) if (now := plan.get("now")) else [],
        "limiter_now": _dim_name(now, now["limiter"]) if now else None,
        "engine_draft_readiness_at_taper": _dims(proj) if (proj := plan.get("projected")) else [],
        "engine_draft_reachable": plan["reachable"],
        "limits": {"build_week_max_increase_pct": round((BUILD_RATE - 1) * 100),
                   "recovery_week_pct_of_build": [60, 75]},
        "weeks_history": weekly_history(acts, today, HISTORY_WEEKS),
        "bests_90d": bests(acts, today),
        "engine_draft": [{"start": w["start"], "focus": w["focus"], "miles": _r(w["miles"], 1),
                          "gain_ft": _r(w["gain_ft"]), "loss_ft": _r(w["loss_ft"]), "workout": w.get("workout") or "none",
                          "hill_session": w.get("hill_session"), "held_back_by": w["limited_by"]} for w in plan["weeks"]],
    }


def check_packet(engine_plan: dict, plan: dict, approach: str, acts: list[dict], today: date) -> dict:
    """Context for checking the coach's plan: what the builder saw, plus the plan as the engine
    held it, its projected readiness and the builder's approach."""
    proj = plan.get("projected")
    return build_packet(engine_plan, acts, today) | {
        "coach_approach": approach,
        "plan_to_check": [_week(w) | {"workout": w.get("workout") or "none", "hill_session": w.get("hill_session"),
                                      "coach_note": w.get("coach_note")} for w in plan["weeks"]],
        "readiness_projected_at_taper": _dims(proj) if proj else [],
        "limiter_projected": _dim_name(proj, proj["limiter"]) if proj else None,
        "reachable": plan["reachable"],
    }


def _planned(it: dict) -> dict:
    return {"kind": it["kind"], "title": it["title"], "distance_mi": _r(it["miles"], 1), "gain_ft": _r(it["gain_ft"]),
            "loss_ft": _r(it["loss_ft"]), "hours": _r((it.get("minutes") or 0) / 60, 1), "benefit": it.get("benefit")}


def fit_packet(items: list[dict], acts: list[dict], week: dict, objective: dict, flags: list[str],
               replan: list[str], today: date) -> dict:
    """Context for reading the last few days against the plan: each day's planned session and what
    was done, runs the plan didn't have, and this week's target, done and still scheduled."""
    by_id = {a["id"]: a for a in acts}
    days = sorted({it["day"] for it in items} | {a["day"] for a in acts})
    matched = {it["activity"]["id"] for it in items if it.get("activity")}
    recent = []
    for d in days:
        planned = [it for it in items if it["day"] == d and it["kind"] != "rest"]
        done = [_activity_line(by_id[it["activity"]["id"]]) for it in planned
                if it.get("activity") and it["activity"]["id"] in by_id]
        recent.append({"day": d, "planned": [_planned(it) for it in planned] or None, "done": done,
                       "status": next((it["status"] for it in planned), "rest")})
    dims = ("miles", "gain_ft", "loss_ft")
    name = {"miles": "distance_mi", "gain_ft": "gain_ft", "loss_ft": "loss_ft"}

    def vol(v: dict | None) -> dict | None:
        return None if v is None else {name[k]: _r(v[k], 1 if k == "miles" else 0) for k in dims}

    return {
        "today": today.isoformat(),
        "objective": objective,
        "recent_days": recent,
        "unplanned": [_activity_line(a) for a in acts if a["id"] not in matched],
        "this_week": {"start": week["start"], "focus": (week.get("target") or {}).get("focus"),
                      "target": vol(week.get("target")), "done": vol(week.get("done")),
                      "still_scheduled": vol(week.get("ahead"))},
        "flags": flags,
        "replan_suggested": replan,
    }


def rules_fit(packet: dict) -> dict:
    """The plan fit from engine numbers alone."""
    def size(v: dict) -> str:
        return f"{v['distance_mi']} mi with {v['gain_ft']:,} ft up"

    days = [d for d in packet["recent_days"] if d["done"]]
    last = days[-1] if days else None
    if last and last["planned"]:
        did, plan = last["done"][-1], last["planned"][0]
        ratio = (did["distance_mi"] or 0) / plan["distance_mi"] if plan["distance_mi"] else 1.0
        how = "about as planned" if 0.85 <= ratio <= 1.15 else "short of the plan" if ratio < 0.85 else "over the plan"
        helps = f"Your {did['day']} run, {size(did)}, came in {how} against its {plan['title']} of {size(plan)}."
        if plan.get("benefit"):
            helps += " " + plan["benefit"]
    elif packet["unplanned"]:
        u = packet["unplanned"][-1]
        helps = f"Your {u['day']} run, {size(u)}, wasn't on the plan; it counts toward the week."
    else:
        missed = [d for d in packet["recent_days"] if d["status"] == "missed"]
        helps = ("The plan's " + missed[-1]["planned"][0]["title"] + f" on {missed[-1]['day']} has no run against it yet."
                 if missed else "No runs in the last few days to read against the plan.")
    w = packet["this_week"]
    if w["target"] and w["done"]:
        week = (f"This week: {size(w['done'])} done of {size(w['target'])}"
                + (f", with {size(w['still_scheduled'])} still scheduled." if w["still_scheduled"] and w["still_scheduled"]["distance_mi"] else "."))
    else:
        week = "No plan target for this week."
    upcoming = [d for d in packet["recent_days"] if d["status"] in ("today", "planned") and not d["done"] and d["planned"]]
    if packet["flags"]:
        nxt = "A load is flagged today: keep the next days easy rather than pushing the plan."
    elif upcoming:
        nxt = f"Carry on with the plan: {upcoming[0]['planned'][0]['title']} next. Don't make up missed miles."
    else:
        nxt = "Carry on with the plan, and don't make up missed miles."
    if packet["replan_suggested"]:
        nxt += " A new plan is suggested on the Plan page: " + "; ".join(packet["replan_suggested"]).lower() + "."
    return {"helps": helps, "week": week, "next": nxt}


def _brief_week(w: dict | None) -> dict | None:
    return None if w is None else {"focus": w["focus"], "distance_mi": _r(w["miles"], 1), "gain_ft": _r(w["gain_ft"]),
                                   "loss_ft": _r(w["loss_ft"]), "hours": _r(w["hours"], 1)}


def review_packet(review: dict, next_week: dict | None, objective: dict | None, replan_text: list[str]) -> dict:
    a, t, sh = review["actual"], review["target"], review["share"]
    return {
        "week": {"start": review["start"], "end": review["end"], "activities": a["activities"],
                 "actual": {"distance_mi": _r(a["miles"], 1), "gain_ft": _r(a["gain_ft"]), "loss_ft": _r(a["loss_ft"]),
                            "hours": _r(a["hours"], 1),
                            "below_aet_pct": None if a["below_aet_pct"] is None else _r(a["below_aet_pct"] * 100),
                            "longest_day_effort_mi": _r(a["longest_day_effort_mi"], 1)},
                 "target": None if not t else {"focus": t["focus"], "distance_mi": _r(t["miles"], 1),
                                               "gain_ft": _r(t["gain_ft"]), "loss_ft": _r(t["loss_ft"]),
                                               "hours": _r(t["hours"], 1)},
                 "pct_of_target": None if not sh else {
                     {"miles": "distance", "gain_ft": "gain", "loss_ft": "loss"}[k]: None if v is None else _r(v * 100)
                     for k, v in sh.items()},
                 "flags": [f"{f['ledger']}: {f['text']}" for f in review["flags"]],
                 "vs_normal_pct_at_end": {k: None if v is None else _r(v * 100)
                                          for k, v in review["vs_normal_at_end"].items()},
                 "max_pain": review["max_pain"]},
        "replan_suggested": replan_text,
        "next_week": None if next_week is None else _week(next_week),
        "objective": objective,
    }


def chat_packet(question: str, history: list[dict], today: dict | None, plan: dict | None, now: dict | None,
                sessions: list[dict], last_14_days: list[dict], day: date,
                activities: list[dict] | None = None) -> dict:
    """Context for one chat answer. The question and the last few turns are in the packet, so a
    number the athlete typed may be quoted back. activities is the athlete's full list, for the
    weekly totals and biggest days."""
    acts = last_14_days if activities is None else activities
    week = None
    if plan:
        from .plan import plan_for_day

        w = plan_for_day(plan, day)
        week = None if w is None else _week(w)
    return {
        "date": day.isoformat(),
        "athlete": None if not today else _athlete(today["thresholds"], today.get("zone_model")),
        "readiness_today": None if not today else {k: {"state": today["readiness"][k]["state"],
                                                       "reason": today["readiness"][k]["reason"]}
                                                   for k in ("engine", "legs")},
        "readiness_score_today": None if not today else _score(today.get("score")),
        "todays_call": None if not today else {k: today["call"][k] for k in ("call", "session", "detail", "why")},
        "checkin": None if not today else today["checkin"],
        "overnight": None if not today else _overnight(today.get("wellness")),
        "ledgers": None if not today else {name: _ledger(d, name) for name, d in today["ledgers"].items()},
        "week_so_far": None if not today else _week_so_far(today["week"]),
        "intensity_28d_pct": None if not today else _intensity(today),
        "objective": None if not plan else _objective(plan, day),
        "objective_readiness_now": None if not now else _dims(now),
        "plan_this_week": week,
        "next_7_days": [{"day": s["day"], "session": s["kind"], "distance_mi": _r(s["miles"], 1),
                         "gain_ft": _r(s["gain_ft"]), "loss_ft": _r(s["loss_ft"]), "minutes": _r(s["minutes"])}
                        for s in sessions],
        "last_14_days": [_activity_line(a) for a in sorted(last_14_days, key=lambda a: a["start_time"])],
        "weeks": weekly_history(acts, day, HISTORY_WEEKS),
        "bests_90d": bests(acts, day),
        "rest": rest_pattern(acts, day),
        "conversation": [{"role": m["role"], "text": m["text"]} for m in history],
        "question": question,
    }


def rules_plan(packet: dict) -> dict:
    """The plan explained from engine numbers alone."""
    if not packet["objective"].get("event", True):
        return _rules_block(packet)
    now = packet["readiness_now"]
    ready = [d["dimension"].lower() for d in now if d["status"] == "ready"]
    behind = [d["dimension"].lower() for d in now if d["status"] in ("close", "gap")]
    verdict = ("Ready now on " + ", ".join(ready) + "." if ready else "No dimension is ready yet.")
    if behind:
        verdict += " Behind on " + ", ".join(behind) + "."
    now, proj = packet["limiter_now"], packet["limiter_projected"]
    if proj:
        limiter = f"{proj} is the limiter: it is still furthest behind its rule at the taper."
    elif now:
        limiter = f"{now} is the limiter now. The long days and peak weeks close it by the taper."
    else:
        limiter = "Nothing is behind its rule."
    weeks = packet["weeks"]
    peak = max(weeks, key=lambda w: w["gain_ft"]) if weeks else None
    text = f"{len(weeks)} weeks to the objective." if len(weeks) != 1 else "The objective is this week."
    if peak:
        start = date.fromisoformat(peak["start"])
        text += (f" The biggest week starts {start:%b} {start.day}: {peak['distance_mi']} mi, {peak['gain_ft']:,} ft up and "
                 f"{peak['loss_ft']:,} ft down.")
    held = [w["start"] for w in weeks if w["held_back_by_load_limit"]]
    if held:
        text += " Some weeks are held back to stay inside the load limits."
    if not packet["reachable"]:
        text += " The objective can't be reached inside the load limits by its date."
        if packet["options"]["later_date"]:
            later = date.fromisoformat(packet["options"]["later_date"])
            text += f" It can be by {later:%B} {later.day}, {later.year}."
    return {"verdict": verdict, "limiter": limiter, "plan": text}


def _rules_block(packet: dict) -> dict:
    """A block with no event, explained from engine numbers alone."""
    o, recent, weeks = packet["objective"], packet["recent_per_week"], packet["weeks"]
    t = o["weekly_targets"]
    verdict = (f"You've averaged {recent['distance_mi']} mi and {recent['gain_ft']:,} ft up a week over the last four "
               f"weeks; the plan's weekly target is {t['distance_mi']} mi and {t['gain_ft']:,} ft up.")
    held = [w for w in weeks if w["held_back_by_load_limit"]]
    limiter = ("Some weeks are held back to stay inside the load limits." if held
               else "Nothing holds the plan back: every week fits the load limits.")
    text = (f"{o['goal']}: {len(weeks)} weeks of building toward the target and holding it, with a recovery week "
            "every few weeks.")
    if o.get("year_totals_target") and not packet["reachable"]:
        text += " At this pace the year's target isn't reached inside the load limits."
    return {"verdict": verdict, "limiter": limiter, "plan": text}


def rules_review(packet: dict) -> dict:
    w = packet["week"]
    a = w["actual"]
    week = f"{a['distance_mi']} mi with {a['gain_ft']:,} ft up and {a['loss_ft']:,} ft down in {a['hours']} h."
    if w["target"]:
        t = w["target"]
        week += f" The target was {t['distance_mi']} mi, {t['gain_ft']:,} ft up and {t['loss_ft']:,} ft down."
    if a["below_aet_pct"] is not None:
        week += f" {a['below_aet_pct']}% of the time was below AeT."
    if w["flags"]:
        week += " Flags: " + "; ".join(w["flags"]) + "."
    if packet["replan_suggested"]:
        nxt = "A new plan is suggested: " + "; ".join(packet["replan_suggested"]).lower() + "."
    elif packet["next_week"]:
        n = packet["next_week"]
        nxt = (f"Next week is a {n['focus']} week: {n['distance_mi']} mi, {n['gain_ft']:,} ft up and "
               f"{n['loss_ft']:,} ft down.")
    else:
        nxt = "No plan for next week."
    return {"week": week, "next": nxt}


CHAT_FALLBACK = ("I can't answer that from your numbers right now. Ask about your loads, your plan or your "
                 "objective, or try again.")


def brief_reply(raw: dict, rule_call: dict, aet: float) -> dict:
    """The card shown on Today: the coach's call and reasoning, with the engine's session for that
    call. The coach never writes the session, so it can't prescribe more than the call allows."""
    from .readiness import cautious_session

    session = ({k: rule_call[k] for k in ("session", "detail")} if raw["call"] == rule_call["call"]
               else cautious_session(raw["call"], aet))
    return {"call": raw["call"], **session, **{k: raw.get(k) for k in BRIEF_FIELDS}}


def _size(v: dict) -> str:
    return f"{v['distance_mi']} mi and {v['gain_ft']:,} ft up"


def rules_focus(packet: dict) -> str:
    """How to do today's session well, from engine numbers alone."""
    call, aet = packet["rule_call"]["call"], packet["athlete"]["hr_aet"]
    legs = packet["readiness"]["legs"]
    planned = (packet.get("plan") or {}).get("today_planned")
    below = (packet.get("intensity_28d_pct") or {}).get("below_aet")
    if call == "rest":
        return "Make the rest count: keep any walking flat and easy, and get to bed early."
    if legs["state"] != "fresh" and legs["limiter"] == "descent":
        return f"Spare your quads: pick flat ground or walk anything steep downhill, and keep heart rate under **{aet} bpm**."
    if legs["state"] != "fresh" and legs["limiter"] == "climb":
        return f"Hike the climbs rather than run them, and keep heart rate under **{aet} bpm**."
    if call == "go" and planned and planned.get("benefit"):
        how = ("Keep the hard part hard and everything around it easy. " if planned.get("kind") in
               ("tempo", "intervals", "hills") else "")
        return how + f"Today is for this: {planned['benefit'][0].lower()}{planned['benefit'][1:]}"
    if below and below < 80:  # 0 means no heart rate in the window, not an all-hard month
        return (f"Keep easy running under **{aet} bpm**: **{below}%** of your last 28 days was below AeT, "
                "under the 80% target.")
    return f"Keep easy running under **{aet} bpm**, and hike any climb that pushes you over it."


def rules_ahead(packet: dict) -> str:
    """Where today sits in the week and the plan, from engine numbers alone."""
    plan = packet.get("plan") or {}
    parts = []
    o = plan.get("objective")
    if o and o["event"] and o.get("days_to_go", -1) >= 0:
        parts.append(f"**{o['days_to_go']} days** to your {o['goal'].lower()}.")
    w = plan.get("this_week")
    if w and w["target"] and w["done"]:
        still = w["still_scheduled"]
        parts.append(f"This week: {_size(w['done'])} done of {_size(w['target'])}"
                     + (f", with {_size(still)} still scheduled." if still and still["distance_mi"] else "."))
    else:
        wk = packet["week_so_far"]
        last = wk.get("same_days_last_week")
        parts.append(f"This week so far: {_size(wk)}"
                     + (f", against {_size(last)} by this day last week." if last else "."))
    nxt = next(((d, d["planned"][0]) for d in plan.get("next_days") or [] if d["planned"]), None)
    if nxt:
        parts.append(f"Next up: {nxt[1]['title']} on {nxt[0]['weekday']}.")
    streak = packet["rest"]["training_days_in_a_row_before_today"]
    if streak >= 6 and packet["rule_call"]["call"] != "rest":
        parts.append(f"You've trained {streak} days in a row: a rest day soon lets the loads settle.")
    return " ".join(parts)


def rules_brief(packet: dict) -> dict:
    """The brief from engine numbers alone: the rule-based call with its focus and what's ahead."""
    return {**packet["rule_call"], "focus": rules_focus(packet), "ahead": rules_ahead(packet)}


def packet_hash(packet: dict) -> str:
    return hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest()[:16]


# Number validator --------------------------------------------------------------------------

NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    return [m.group().rstrip(",") for m in NUMBER.finditer(text)]


def allowed_numbers(packet: Any) -> set[float]:
    """Every number in the packet, including ones inside strings (the rule call's '60 to 75 min')."""
    out: set[float] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.add(abs(float(v)))
        elif isinstance(v, str):
            out.update(float(t.replace(",", "")) for t in numbers_in(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(str(k).replace("_", " "))  # keys carry numbers too: minutes_above_8000ft, the "8-20" grade band
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(packet)
    return out


def traceable(token: str, allowed: set[float]) -> bool:
    """A number is traceable when some packet number rounds to it at the precision it was
    written: '1.5' for 1.47, and '4,500' for 4,512 when within ROUNDING_SLACK of it."""
    plain = token.replace(",", "")
    x = float(plain)
    decimals = len(plain.split(".")[1]) if "." in plain else 0
    zeros = len(plain) - len(plain.rstrip("0")) if decimals == 0 else 0
    for v in allowed:
        if abs(round(v, decimals) - x) < 1e-9:
            return True
        for k in range(1, min(zeros, 3) + 1):
            if abs(round(v, -k) - x) < 1e-9 and abs(v - x) <= ROUNDING_SLACK * x:
                return True
    return False


# The prompt's own windows and scales ("42-day normal", "60-day baseline", 100 = an hour at AnT).
# Small counts in it (1 to 8) stay out, so they still have to come from the packet.
PROMPT_NUMBERS = {x for x in allowed_numbers(SYSTEM) if x >= 10}


def _plausible_run(a: Any) -> bool:
    """A schedule_run whose size is inside the limits the engine holds its cards to."""
    if not isinstance(a, dict) or a.get("kind") != "schedule_run":
        return False
    miles, gain, minutes = a.get("miles") or 0, a.get("gain_ft") or 0, a.get("minutes")
    try:
        return (0 <= miles <= 100 and 0 <= gain <= 30000 and bool(miles or minutes)
                and (minutes is None or 0 < minutes <= 2880))
    except TypeError:
        return False


def check(reply: dict, packet: dict, mode: str) -> list[str]:
    """Problems with a reply, as sentences the model can act on. Empty means it passes."""
    if mode in FIELD_MODES:
        return []
    if MODES[mode].get("free_numbers"):
        return _week_problems(reply, packet, mode)
    allowed = allowed_numbers(packet) | PROMPT_NUMBERS
    # A proposed run carries new numbers (its size), which the athlete confirms on its card; the
    # answer may quote them, and every other number still has to come from the data. Only where
    # actions can be proposed (the packet lists scheduled_runs), and only a run whose size could
    # become a card, so an action can't carry an invented number past the check.
    if "scheduled_runs" in packet:
        allowed |= allowed_numbers([{k: a.get(k) for k in ("miles", "gain_ft", "minutes", "day")}
                                    for a in reply.get("actions") or [] if _plausible_run(a)])
    bad = []
    for key, value in reply.items():
        if key == "actions":
            continue
        for tok in numbers_in(str(value)):
            if not traceable(tok, allowed) and tok not in bad:
                bad.append(tok)
    problems = []
    if bad:
        problems.append("These numbers are not in the data: " + ", ".join(bad) + ".")
    if mode == "brief":
        rules = packet["rule_call"]["call"]
        if CALL_ORDER.index(reply["call"]) < CALL_ORDER.index(rules):
            problems.append(f"The call may not be less cautious than the rule-based call ({rules}).")
    return problems


def _week_problems(reply: dict, packet: dict, mode: str) -> list[str]:
    """A built or revised plan has to line up with the engine's weeks, so the engine can check it.
    A check that finds the plan sound sends no weeks."""
    if "engine_draft" not in packet or "weeks" not in reply:
        return []
    weeks = reply["weeks"] or []
    if mode == "check" and reply.get("ok") and not weeks:
        return []
    starts = [w["start"] for w in packet["engine_draft"]]
    if mode == "check" and not weeks:
        return ["ok is false but weeks is empty: send the whole revised plan, or set ok to true."]
    if [w.get("start") for w in weeks] != starts:
        return [f"weeks must have exactly {len(starts)} entries, one per engine_draft week in the same order, "
                f"starting {starts[0]} and ending {starts[-1]}."]
    return []


# Rule-based fallbacks ----------------------------------------------------------------------

def rules_analysis(packet: dict) -> dict:
    """The five answers from engine numbers alone, for when the coach is off or fails."""
    act, loads = packet["activity"], packet["loads"]
    zp = act["zone_pct"]
    job = f"{act['distance_mi']} mi with {act['gain_ft']:,} ft up and {act['loss_ft']:,} ft down."
    if zp:
        parts = [f"{v}% in Zone {k[1:]}" for k, v in zp.items()]
        job += " Time in zone: " + ", ".join(parts[:-1]) + f" and {parts[-1]}."
    hot = [s for s in packet["segments"] if s["above_ant_min"] is not None and s["above_ant_min"] >= 1]
    if hot:
        worst = max(hot, key=lambda s: s["above_ant_min"])
        off = f"{worst['label']} had the most time above AnT: {worst['above_ant_min']} min."
    else:
        off = "Nothing stood out: no segment spent a minute above AnT." if zp else "No heart rate, so no zone data."
    scored = {k: v for k, v in loads.items() if v["times_usual_day"] is not None}
    parts = [f"{k} {v['times_usual_day']}x a usual day" + (" (6-week high)" if v["six_week_high"] else "")
             for k, v in scored.items()]
    cost = ("Loads: " + ", ".join(parts) + ".") if parts else "No loads scored."
    top = max(scored, key=lambda k: scored[k]["times_usual_day"]) if scored else None
    if top and scored[top]["times_usual_day"] >= 1.5:
        changes = f"{top.capitalize()} took the biggest hit. Keep the next couple of days light on {top}."
    else:
        changes = "No load ran well above a usual day, so no change to the next few days."
    band_name = {"<8": "under 8%", "8-20": "8 to 20%", ">20": "over 20%"}
    gain = act["gain_by_grade_ft"]
    if act["gain_ft"] and gain:
        band = max(gain, key=gain.get)
        takeaway = f"Most of the climbing came on {band_name.get(band, band)} grades: {gain[band]:,} ft."
    else:
        takeaway = "A flat day: no climbing to speak of."
    dur = packet.get("durability")
    if dur and dur["verdict"] == "faded":
        drift = (f"heart rate drifted {dur['decoupling_pct']}% against grade-adjusted pace"
                 if dur["decoupling_pct"] is not None else
                 f"grade-adjusted pace slowed {dur['grade_adjusted_pace_change_pct']}%")
        off = f"The second half faded: {drift}. " + off
    moved = [g for g in packet.get("grades", []) if g["usual_90d"] and g["usual_90d"]["rate_vs_usual_pct"] is not None
             and abs(g["usual_90d"]["rate_vs_usual_pct"]) >= 8]
    if moved:
        g = max(moved, key=lambda g: g["vert_ft"])  # the grade that carried the most vert
        u = g["usual_90d"]
        verb = "climbed" if g["grade"].startswith("climb") else "descended"
        takeaway += (f" On {g['grade'].split(' ', 1)[1]} grades you {verb} at {g['vert_rate_ft_per_hour']:,} ft/h, "
                     f"{abs(u['rate_vs_usual_pct'])}% {'faster' if u['rate_vs_usual_pct'] > 0 else 'slower'} "
                     f"than your usual {u['vert_rate_ft_per_hour']:,} ft/h.")
    return {"job": job, "off": off, "cost": cost, "changes": changes, "takeaway": takeaway}


# Calling Claude ----------------------------------------------------------------------------

class Client(Protocol):
    beta: Any


@dataclass
class Result:
    source: str  # "coach" or "rules"
    reply: dict
    model: str | None = None
    attempts: int = 0
    fallback_reason: str | None = None  # why rules were shown: no_key, rejected, refusal, api_error
    rejected: list[str] = field(default_factory=list)
    usage: dict | None = None


def default_client() -> Client | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    return anthropic.Anthropic()


def default_model(mode: str) -> str:
    return os.environ.get("TALUS_COACH_MODEL") or (BRIEF_MODEL if mode == "brief" else ANALYSIS_MODEL)


def cache_key(packet: dict, model_id: str, style: str = DEFAULT_STYLE) -> str:
    """A reply is reused only for the same data, model and style."""
    extra = {"_model": model_id} | ({} if style == DEFAULT_STYLE else {"_style": style})
    return packet_hash({**packet, **extra})


def _fallback_args(model_id: str) -> dict:
    """Haiku has no server-side fallback, so its requests go without it."""
    if model_id.startswith("claude-haiku"):
        return {}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def _ask(client: Client, mode: str, packet: dict, problems: list[str], model_id: str,
         style: str = DEFAULT_STYLE) -> tuple[Any, dict | None]:
    cfg = MODES[mode]
    text = f"Mode: {cfg['label']}.\n"
    if cfg.get("prompt"):
        text += cfg["prompt"] + "\n"
    if STYLES.get(style, STYLES[DEFAULT_STYLE])["prompt"]:
        text += STYLES[style]["prompt"] + "\n"  # after the cached system prompt, so every style shares it
    text += "Data:\n" + json.dumps(packet, sort_keys=True)
    if problems:
        text += ("\n\nYour last reply was rejected. " + " ".join(problems)
                 + (" Write it again." if cfg.get("free_numbers") else " Write it again using only numbers that appear in the data."))
    resp = client.beta.messages.create(
        model=model_id,
        max_tokens=cfg["max_tokens"],
        **_fallback_args(model_id),
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": cfg["effort"], "format": {"type": "json_schema", "schema": cfg["schema"]}},
        messages=[{"role": "user", "content": text}],
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        return resp, None
    body = next((b.text for b in resp.content if b.type == "text"), None)
    return resp, None if body is None else json.loads(body)


def _usage(resp: Any) -> dict | None:
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    return {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                              "cache_creation_input_tokens")}


def _add_usage(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return a or b
    return {k: (a.get(k) or 0) + (b.get(k) or 0) for k in a.keys() | b.keys()}


def run(mode: str, packet: dict, fallback: dict, client: Client | None, model_id: str | None = None,
        style: str = DEFAULT_STYLE) -> Result:
    """Ask the coach, check every number, retry once, then fall back to rules."""
    model_id = model_id or default_model(mode)
    if client is None:
        return Result("rules", fallback, fallback_reason="no_key")
    import anthropic

    problems: list[str] = []
    rejected: list[str] = []
    usage = None
    for attempt in (1, 2):
        try:
            resp, reply = _ask(client, mode, packet, problems, model_id, style)
        except (anthropic.APIError, ValueError) as e:  # ValueError: bad JSON, or a request the SDK won't send
            return Result("rules", fallback, model_id, attempt, f"api_error: {type(e).__name__}", rejected, usage)
        usage = _add_usage(usage, _usage(resp))  # a retry is billed too
        if reply is None:
            return Result("rules", fallback, resp.model, attempt, str(resp.stop_reason), rejected, usage)
        problems = check(reply, packet, mode)
        if not problems:
            return Result("coach", reply, resp.model, attempt, None, rejected, usage)
        rejected.extend(problems)
    return Result("rules", fallback, resp.model, 2, "rejected", rejected, usage)
