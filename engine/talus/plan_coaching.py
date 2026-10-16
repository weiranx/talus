"""Deterministic, reviewable session changes and evidence for conditional projections."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from . import plan as planner

REASONS = {
    "busy": "Less time / busy", "too_hard": "Workout was too hard", "sore": "Legs still sore",
    "terrain": "Terrain unavailable", "availability": "Update availability",
}
HARD = {"long", "long2", "hills", *planner.WORKOUTS}
FIELDS = ("kind", "miles", "gain_ft", "loss_ft", "minutes")


def session(s: dict) -> dict:
    return {k: s.get(k, 0.0) for k in FIELDS} | {"day": s["day"]}


def rest(s: dict) -> dict:
    return s | {"kind": "rest", **dict.fromkeys(FIELDS[1:], 0.0)}


def easy(s: dict, factor: float) -> dict:
    out = s | {"kind": "easy"}
    for k in FIELDS[1:]:
        out[k] = s[k] * factor
    # Remove strides, strength, and old workout metadata as well as intensity.
    for k in ("type", "strength", "workout", "night", "longest_descent_ft"):
        out.pop(k, None)
    return out


def revise(plan: dict, acts: list[dict], today: date, own: dict, reason: str, day: date,
           limits: dict, pain: bool = False) -> dict:
    """Freeze the reviewed fortnight as dated overrides; never increase its weekly totals.

    Manual sessions and date locks stay fixed. Moving a key session swaps with an easy/rest
    day in the same week, only when time, terrain, spacing and weekly volume all fit.
    """
    before = planner.upcoming_sessions(plan, acts, today, 14, own)
    if not before or day.isoformat() not in {s["day"] for s in before}:
        raise ValueError("Choose a day in the next two weeks of your plan.")
    c = planner.Constraints.from_json(plan["constraints"] | {"date_limits": limits})
    after = deepcopy(before)
    notes: dict[str, str] = {}
    fixed = set(own) | {d for d, v in limits.items() if v.get("locked")}
    completed = {a["day"] for a in acts if planner.is_foot(a["sport"])}
    fixed |= completed
    objective_day = plan["objective"]["day"] if planner.is_event(plan["objective"]) else None
    if objective_day:
        fixed.add(objective_day)

    def change(i: int, value: dict, why: str) -> None:
        d = after[i]["day"]
        if d in fixed and session(value) != session(after[i]):
            raise ValueError(f"{d} is completed, fixed, or your own session. Unlock or edit that session first.")
        after[i] = value
        notes[d] = why

    index = next(i for i, s in enumerate(after) if s["day"] == day.isoformat())
    if reason in ("sore", "too_hard") or pain:
        end = day + timedelta(days=2 if reason == "sore" or pain else 1)
        for i, s in enumerate(after):
            if day.isoformat() <= s["day"] <= end.isoformat() and s["kind"] not in ("done", "objective", "rest"):
                change(i, rest(s) if pain else easy(s, 0.5 if reason == "sore" else 0.7),
                       "Rest after the pain check-in." if pain else
                       "Ease the next sessions for sore legs." if reason == "sore" else
                       "Replace intensity with shorter easy running after a difficult workout.")
    elif reason == "busy":
        change(index, rest(after[index]), "Take this day off for your schedule.")
    elif reason == "terrain":
        s = after[index]
        # Preserve time on feet at easy effort; removing vert must not add distance.
        change(index, easy(s, 1.0) | {"gain_ft": 0.0, "loss_ft": 0.0,
                                     "minutes": s["miles"] * plan["pace"]["min_per_effort_mi"]},
               "Use flat easy running; move the mountain-specific work only if another day fits.")

    # Apply each date's cap to its session, without redistributing the shortfall.
    for i, s in enumerate(after):
        d = date.fromisoformat(s["day"])
        if s["kind"] in ("done", "objective", "rest"):
            continue
        cap = c.cap(d) if c.available(d) else 0
        flat = limits.get(s["day"], {}).get("terrain") == "flat"
        if cap == 0:
            change(i, rest(s), "Unavailable on this date.")
        elif flat and (s["gain_ft"] or s["loss_ft"] or s["kind"] == "hills"):
            value = easy(s, 1.0) | {"gain_ft": 0.0, "loss_ft": 0.0}
            value["minutes"] = value["miles"] * plan["pace"]["min_per_effort_mi"]
            if value["minutes"] > cap:
                value = easy(value, cap / value["minutes"])
            change(i, value, "Flat terrain on this date; keep the session easy.")
        elif s["minutes"] > cap:
            value = easy(s, cap / s["minutes"])
            change(i, value, "Shorten the session to this date's time limit.")

    # Preserve a displaced key workout for scheduling reasons when a whole swap fits.
    if not pain and reason in ("busy", "terrain", "availability"):
        for i, original in enumerate(before):
            if original["kind"] not in HARD or session(original) == session(after[i]):
                continue
            start = date.fromisoformat(original["day"])
            monday = start - timedelta(days=start.weekday())
            for j in sorted(range(len(after)), key=lambda j: abs(j - i)):
                dest = after[j]
                d = date.fromisoformat(dest["day"])
                if j == i or dest["day"] in fixed or dest["kind"] not in ("easy", "rest"):
                    continue
                if not monday <= d < monday + timedelta(days=7) or not c.available(d):
                    continue
                if original["minutes"] > c.cap(d):
                    continue
                if limits.get(dest["day"], {}).get("terrain") == "flat" and (original["gain_ft"] or original["loss_ft"]):
                    continue
                if any(k != i and k != j and s["kind"] in HARD and
                       abs((date.fromisoformat(s["day"]) - d).days) <= 1 for k, s in enumerate(after)):
                    continue
                # Swapping the original and destination can only preserve or reduce volume.
                replacement = (easy(dest, 1.0) if dest["kind"] == "easy" else dest) | {"day": original["day"]}
                origin_cap = c.cap(start) if c.available(start) else 0
                if replacement["minutes"] > origin_cap or (reason == "busy" and i == index):
                    replacement = rest(replacement)
                if limits.get(original["day"], {}).get("terrain") == "flat" and (replacement["gain_ft"] or replacement["loss_ft"]):
                    replacement = rest(replacement)
                change(i, replacement, f"Make room for the key workout on {dest['day']}.")
                change(j, original | {"day": dest["day"]}, f"Move the key workout from {original['day']} with recovery space.")
                break

    # A second long day only has its intended job beside the first one. If that pair is
    # broken, remove the tired-legs prescription rather than leaving an orphaned label.
    by_day = {s["day"]: s for s in after}
    for i, s in enumerate(after):
        if s["kind"] != "long2":
            continue
        prior = (date.fromisoformat(s["day"]) - timedelta(days=1)).isoformat()
        previous = by_day.get(prior)
        actual_effort = sum(a["effort_mi"] for a in acts if a["day"] == prior and planner.is_foot(a["sport"]))
        completed_pair = planner.pair_size(actual_effort, planner._effort(s)) > 0
        if (previous is None or previous["kind"] != "long") and not completed_pair:
            change(i, easy(s, 0.7), "The back-to-back pair was broken; use shorter easy running instead of a second long day.")

    changes = [{"day": a["day"], "before": session(b), "after": session(a), "why": notes.get(a["day"], REASONS[reason])}
               for b, a in zip(before, after) if session(b) != session(a)]
    weeks = []
    for start in sorted({(date.fromisoformat(s["day"]) - timedelta(days=date.fromisoformat(s["day"]).weekday())).isoformat()
                         for s in before}):
        end = (date.fromisoformat(start) + timedelta(days=7)).isoformat()
        totals = lambda ss: {k: round(sum(s[k] for s in ss if start <= s["day"] < end and s["kind"] not in ("done", "objective")), 2)
                             for k in FIELDS[1:]}
        b, a = totals(before), totals(after)
        if any(a[k] > b[k] + 0.01 for k in FIELDS[1:]):
            raise ValueError("This change would increase the week's training. Try another date.")
        weeks.append({"start": start, "before": b, "after": a})
    updated = deepcopy(plan)
    updated["constraints"]["date_limits"] = limits
    overrides = updated.setdefault("session_overrides", {})
    # Hold the exact previewed sessions, including unchanged dates, so acceptance matches the preview.
    for s in after:
        if s["kind"] not in ("done", "objective") and s["day"] not in own:
            overrides[s["day"]] = session(s) | {k: s[k] for k in ("type", "strength", "night", "race", "longest_descent_ft") if k in s} | {
                "revision": True, "change_reason": notes.get(s["day"])}
    for iso, limit in limits.items():
        if not limit.get("locked") or iso <= after[-1]["day"] or iso > plan["objective"]["day"]:
            continue
        future = planner.upcoming_sessions(plan, acts, date.fromisoformat(iso), 1, own)
        if not future or future[0]["kind"] == "objective":
            continue
        s = future[0]
        if s["minutes"] > limit["minutes"] or (limit.get("terrain") == "flat" and (s["gain_ft"] or s["loss_ft"])):
            raise ValueError(f"The fixed session on {iso} does not fit this time or terrain. Unlock it before revising.")
        overrides[iso] = session(s) | {k: s[k] for k in ("type", "strength", "night", "race", "longest_descent_ft") if k in s} | {
            "revision": True, "change_reason": "Keep this session fixed."}
    # Full future weeks in the preview already include the engine's volume adaptation.
    # Keep their reviewed targets with their fixed sessions, rather than cutting twice.
    pinned = set(updated.get("revision_weeks", []))
    for w in updated["weeks"]:
        start = date.fromisoformat(w["start"])
        if start < today or start + timedelta(days=6) > date.fromisoformat(after[-1]["day"]):
            continue
        ss = [s for s in after if w["start"] <= s["day"] <= (start + timedelta(days=6)).isoformat()
              and s["kind"] not in ("done", "objective")]
        for k in planner.DIMS:
            w[k] = sum(s[k] for s in ss)
        w["hours"] = sum(s["minutes"] for s in ss) / 60
        w["below_aet_hours"] = w["hours"] * 0.8
        old_adaptation = w.pop("adapted", None)
        w["notes"] = [n for n in w.get("notes", []) if n != (old_adaptation or {}).get("text")]
        w["notes"].append("Sessions and targets held at the accepted revision; review again if training changes")
        pinned.add(w["start"])
    updated["revision_weeks"] = sorted(pinned)
    impact = "The weekly totals shown cover the remaining sessions. Reduced work is left out; later progress depends on completing and recovering from the revised sessions."
    if any(c["before"]["kind"] in ("long", "long2", "hills") for c in changes):
        impact += " Mountain-specific or long-day practice changed; check that milestone before relying on the original readiness projection."
    return {"after_plan": updated, "changes": changes, "weeks": weeks, "impact": impact}


def evidence(plan: dict, acts: list[dict], today: date, outcomes: list[dict], sessions: list[dict]) -> dict:
    recent = [a for a in acts if (today - timedelta(days=56)).isoformat() <= a["day"] <= today.isoformat()
              and planner.is_foot(a["sport"])]
    days = sorted({a["day"] for a in recent})
    missing = []
    if not plan["pace"]["learned"]:
        missing.append("Pace uses a default; at least three longer activities are needed to learn yours.")
    if len(days) < 12 or not days or (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days < 28:
        missing.append("Training history is short or sparse for the recent-capacity comparison.")
    if not days or (today - date.fromisoformat(days[-1])).days > 7:
        missing.append("No activity in the last seven days; check that your sync is current.")
    if planner.is_event(plan["objective"]):
        if plan["objective"].get("longest_descent_ft") is None:
            missing.append("The objective's longest continuous descent is not supplied.")
        if not any(a.get("longest_descent_ft") is not None for a in recent):
            missing.append("Recent activities have no measured continuous-descent evidence.")
    if not outcomes:
        missing.append("Workout intent and recovery have no athlete feedback yet.")
    long = next((s for s in sessions if s["kind"] in ("long", "long2", "hills")), None)
    assumptions = ["The projection assumes the planned build, long days and recovery weeks are completed.",
                   "Training limits and readiness rules are starting heuristics, not a validated probability of finishing.",
                   "Freshness and workout feedback describe evidence quality; missing inputs do not count as zero fitness."]
    if plan.get("session_overrides"):
        assumptions.append("An accepted revision holds the reviewed sessions and full-week targets. Review again when training or availability changes.")
    if plan.get("session_overrides") or any(w.get("adapted") for w in plan["weeks"]):
        assumptions.insert(0, "The original projection predates calendar adjustments. Rebuild a draft to update the goal projection.")
    return {"label": "Limited evidence" if len(missing) >= 3 else "Developing evidence" if missing else "Supported by recent history",
            "activity_days": len(days), "last_activity": days[-1] if days else None, "missing": missing,
            "assumptions": assumptions,
            "milestone": None if long is None else {"day": long["day"], "kind": long["kind"],
                "text": "Complete this key session, record whether it achieved its purpose, then check in on recovery the following day."}}


def outcomes_summary(outcomes: list[dict]) -> dict:
    efforts = [o["effort"] for o in outcomes if o.get("effort") is not None]
    return {"reported": len(outcomes), "met": sum(o["intent"] == "met" for o in outcomes),
            "partial": sum(o["intent"] == "partial" for o in outcomes),
            "missed": sum(o["intent"] == "missed" for o in outcomes),
            "effort_average": round(sum(efforts) / len(efforts), 1) if efforts else None,
            "sessions": outcomes}
