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
from datetime import date
from typing import Any, Protocol

PROMPT_VERSION = "coach-v0.3"
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

SYSTEM = """You are the coach inside Talus, a training app for mountain runners and hikers.

Talus tracks three separate loads, because mountain training stresses three systems that recover at different rates:
- cardio: heart-rate load (Banister TRIMP, scaled so 1 hour at anaerobic threshold = 100).
- climb: feet gained, weighted by grade band (under 8%, 8 to 20%, over 20%).
- descent: feet lost, weighted by grade band; steep, fast descending costs the most. Descent load is what makes quads sore for days.
For each load, "normal" is the 42-day average per day and "last 7 days" is the 7-day average per day. "vs_normal_pct" is how far last 7 days sits above or below normal. Readiness says whether the engine (cardio) and the legs (worse of climb and descent) are fresh, loaded or overreached, and why. The morning check-in (soreness, energy and pain, each 1 to 5) can only make readiness worse.
Overnight signals from the watch can only make the engine worse, never the legs: HRV is the 7-day average against a 60-day baseline, resting heart rate is last night against a 60-day baseline, and sleep is last night and the 7-day average against the athlete's stated need. A signal with status "no_data" or "building" is missing or has too little history: say so if it matters, and never guess its value.

Your reader is the athlete. They use miles and feet, never km or meters, and they always weigh distance and vert together.

Rules:
1. Every number you write must come from the data you are given, rounded no more finely than it appears there. Do not compute new numbers (no sums, differences, ratios, paces or conversions) and do not invent targets. If you want a number the data lacks, describe it in words instead. Write counts as words ("two days", "three climbs").
2. Never quote GPS, names or anything not in the data.
3. Plain language. Short sentences. Talk to the athlete as "you". No hype, no exclamation marks, no emoji.
4. Ground every claim in a load, a state, a flag, a segment or the check-in, and name it.
5. Pain above 3 of 5 means rest and, if it persists, see a clinician. Never talk an athlete into training through pain.

Daily brief: you get today's readiness, the overnight signals, the three loads, the last 14 days of activities, this week's totals, 28-day intensity and the rule-based call. The engine sets the session; you make the call and explain it:
- call: go, modify or rest. Keep rule_call's call unless something in the data argues for more caution; never be less cautious. If you keep it, the athlete gets rule_call's session; if you choose a more cautious call, they get an easier one.
- why: two or three sentences on what in the data drives the call, naming the loads, states or check-in behind it. Do not prescribe a different session.

Workout analysis: you get one activity with its segments, its three loads against a usual day, and the athlete's current state. Answer five questions, one or two sentences each:
- job: did it do its job? Judge from time in each zone, where the vert came from and the hike share.
- off: where did it go off? Name the segment (as labeled) where heart rate or effort drifted, or say nothing stood out.
- cost: what did it cost? Name the loads, especially any 6-week high or any load well above a usual day.
- changes: what does it change for the next few days? Point to the ledger it pushed hardest.
- takeaway: one thing to keep or change next time.
"""

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "call": {"type": "string", "enum": list(CALL_ORDER)},
        "why": {"type": "string"},
    },
    "required": ["call", "why"],
    "additionalProperties": False,
}

ANALYSIS_FIELDS = ("job", "off", "cost", "changes", "takeaway")
ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in ANALYSIS_FIELDS},
    "required": list(ANALYSIS_FIELDS),
    "additionalProperties": False,
}

MODES = {
    # Short reply, so low effort keeps it quick; analysis gets more thought.
    "brief": {"schema": BRIEF_SCHEMA, "effort": "low", "max_tokens": 6000},
    "analysis": {"schema": ANALYSIS_SCHEMA, "effort": "medium", "max_tokens": 12000},
}


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


def _athlete(th: dict) -> dict:
    return {"hr_resting": _r(th["resting"]), "hr_aet": _r(th["aet"]), "hr_ant": _r(th["ant"]),
            "hr_max": _r(th["max"]), "zones": "zone 1 below AeT, zone 2 AeT to AnT, zone 3 above AnT"}


def _ledger(d: dict) -> dict:
    return {"vs_normal_pct": None if d["vs_normal"] is None else _r(d["vs_normal"] * 100),
            "last_7_days_per_day": _r(d["fatigue"]), "normal_per_day": _r(d["fitness"]),
            "load_today": _r(d["load"]), "flags": [f["text"] for f in d["flags"]]}


def _activity_line(a: dict) -> dict:
    return {"day": a["day"], "sport": a["sport"], "distance_mi": _r(a["distance_mi"], 1),
            "gain_ft": _r(a["gain_ft"]), "loss_ft": _r(a["loss_ft"]), "effort_mi": _r(a["effort_mi"], 1),
            "moving_time": _hm(a["moving_time_s"]), "zone_minutes": _zones(a["zone_minutes"]),
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


def brief_packet(today: dict, last_14_days: list[dict]) -> dict:
    """Compact context for the daily brief, built from the /api/today payload."""
    ready = today["readiness"]
    week = today["week"]
    return {
        "date": today["date"],
        "athlete": _athlete(today["thresholds"]),
        "checkin": today["checkin"],
        "overnight": _overnight(today.get("wellness")),
        "readiness": {"engine": {k: ready["engine"][k] for k in ("state", "reason")},
                      "legs": {k: ready["legs"][k] for k in ("state", "reason", "limiter")}},
        "rule_call": {k: today["call"][k] for k in ("call", "session", "detail", "why")},
        "ledgers": {name: _ledger(d) for name, d in today["ledgers"].items()},
        "week_so_far": {"start": week["start"], "activities": len(week["activities"]),
                        "distance_mi": _r(week["distance_mi"], 1), "gain_ft": _r(week["gain_ft"]),
                        "loss_ft": _r(week["loss_ft"])},
        "intensity_28d_pct": {k: _r(v * 100) for k, v in today["intensity_28d"]["share"].items()},
        "last_14_days": [_activity_line(a) for a in sorted(last_14_days, key=lambda a: a["start_time"])],
    }


def segment_labels(segments: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    out = []
    for s in segments:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
        out.append(f"{s['kind'].capitalize()} {counts[s['kind']]}")
    return out


def analysis_packet(a: dict, thresholds: dict, ledgers_now: dict, today: date) -> dict:
    """Compact context for a workout analysis, built from the /api/activities/{id} payload.
    Streams and GPS are left out; segments carry the shape of the effort."""
    zm = a["zone_minutes"]
    total_z = sum(zm.values()) if zm else 0
    segs = a["segments"][:MAX_SEGMENTS]
    return {
        "athlete": _athlete(thresholds),
        "activity": {
            "day": a["day"], "days_ago": (today - date.fromisoformat(a["day"])).days, "sport": a["sport"],
            "distance_mi": _r(a["distance_mi"], 1), "gain_ft": _r(a["gain_ft"]), "loss_ft": _r(a["loss_ft"]),
            "effort_mi": _r(a["effort_mi"], 1), "moving_time": _hm(a["moving_time_s"]),
            "elapsed_time": _hm(a["elapsed_time_s"]), "hike_share_pct": _r(a["hike_share"] * 100),
            "zone_minutes": _zones(zm),
            "zone_pct": None if not total_z else {k: _r(v / total_z * 100) for k, v in zm.items()},
            "gain_by_grade_ft": {k: _r(v) for k, v in a["gain_by_band_ft"].items()},
            "loss_by_grade_ft": {k: _r(v) for k, v in a["loss_by_band_ft"].items()},
            "longest_climb_ft": _r(a["longest_climb_ft"]), "longest_descent_ft": _r(a["longest_descent_ft"]),
            "minutes_above_8000ft": _r(a["minutes_above_8000ft"]),
            "cardio_estimated_without_hr": a["cardio_estimated"], "notes": a["notes"],
        },
        "loads": {name: {"load": _r(l["load"]), "times_usual_day": _r(l["times_usual_day"], 1),
                         "six_week_high": l["six_week_high"]} for name, l in a["loads"].items()},
        "segments": [{"label": label, "distance_mi": _r(s["distance_mi"], 2), "gain_ft": _r(s["gain_ft"]),
                      "loss_ft": _r(s["loss_ft"]), "avg_grade_pct": _r(s["avg_grade"], 1),
                      "time": _hm(s["time_s"]), "hike_share_pct": _r(s["hike_share"] * 100),
                      "zone_minutes": _zones(s["zone_minutes"]), "climb_load": _r(s["climb_load"]),
                      "descent_load": _r(s["descent_load"])}
                     for label, s in zip(segment_labels(segs), segs)],
        "ledgers_now": {name: _ledger(d) for name, d in ledgers_now.items()},
    }


def brief_reply(raw: dict, rule_call: dict, aet: float) -> dict:
    """The card shown on Today: the coach's call and reasoning, with the engine's session for that
    call. The coach never writes the session, so it can't prescribe more than the call allows."""
    from .readiness import cautious_session

    session = ({k: rule_call[k] for k in ("session", "detail")} if raw["call"] == rule_call["call"]
               else cautious_session(raw["call"], aet))
    return {"call": raw["call"], **session, "why": raw["why"]}


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
            for x in v.values():
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


def check(reply: dict, packet: dict, mode: str) -> list[str]:
    """Problems with a reply, as sentences the model can act on. Empty means it passes."""
    allowed = allowed_numbers(packet)
    bad = []
    for value in reply.values():
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


# Rule-based fallbacks ----------------------------------------------------------------------

def rules_analysis(packet: dict) -> dict:
    """The five answers from engine numbers alone, for when the coach is off or fails."""
    act, loads = packet["activity"], packet["loads"]
    zp = act["zone_pct"]
    job = f"{act['distance_mi']} mi with {act['gain_ft']:,} ft up and {act['loss_ft']:,} ft down."
    if zp:
        job += f" {zp['z1']}% of the time in Zone 1, {zp['z2']}% in Zone 2 and {zp['z3']}% in Zone 3."
    hot = [s for s in packet["segments"] if s["zone_minutes"] and s["zone_minutes"]["z3"] >= 1]
    if hot:
        worst = max(hot, key=lambda s: s["zone_minutes"]["z3"])
        off = f"{worst['label']} had the most time above AnT: {worst['zone_minutes']['z3']} min."
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
    text = f"Mode: {'daily brief' if mode == 'brief' else 'workout analysis'}.\n"
    if STYLES.get(style, STYLES[DEFAULT_STYLE])["prompt"]:
        text += STYLES[style]["prompt"] + "\n"  # after the cached system prompt, so every style shares it
    text += "Data:\n" + json.dumps(packet, sort_keys=True)
    if problems:
        text += ("\n\nYour last reply was rejected. " + " ".join(problems)
                 + " Write it again using only numbers that appear in the data.")
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
        except (anthropic.APIError, json.JSONDecodeError) as e:
            return Result("rules", fallback, model_id, attempt, f"api_error: {type(e).__name__}", rejected, usage)
        usage = _usage(resp)
        if reply is None:
            return Result("rules", fallback, resp.model, attempt, str(resp.stop_reason), rejected, usage)
        problems = check(reply, packet, mode)
        if not problems:
            return Result("coach", reply, resp.model, attempt, None, rejected, usage)
        rejected.extend(problems)
    return Result("rules", fallback, resp.model, 2, "rejected", rejected, usage)
