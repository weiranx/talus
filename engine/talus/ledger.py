"""The three load ledgers: daily load, fitness, fatigue and form for cardio, climb and descent.

Each ledger is kept separately and never blended. Fitness is a 42-day exponentially weighted
average of daily load, fatigue a 7-day one, form is fitness minus fatigue. Flags are prompts
for the coach, not injury predictions; their thresholds are the spec's starting rules.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

LEDGERS = ("cardio", "climb", "descent")
FITNESS_DAYS = 42
FATIGUE_DAYS = 7
RAMP_RATIO = 1.3
OVERREACH_RATIO = 1.5
SPIKE_RATIO = 1.3  # one day 30% larger than any day in the last 42
DETRAINING_DROP = 0.10  # fitness down 10% in 14 days
DETRAINING_DAYS = 14
MIN_HISTORY_DAYS = 28  # ratio flags are noise before the averages warm up
# No activity of any kind for this long means the data is missing (watch not synced, history not
# imported), not that the athlete rested. Shorter stretches, a rest week or two, still count as rest.
GAP_DAYS = 28


@dataclass
class LedgerDay:
    day: date
    ledger: str
    load: float
    fitness: float
    fatigue: float
    flags: list[str] = field(default_factory=list)

    @property
    def form(self) -> float:
        return self.fitness - self.fatigue

    @property
    def vs_normal(self) -> float | None:
        """Last 7 days against your normal: 0.4 reads as "40% above your normal"."""
        return self.fatigue / self.fitness - 1 if self.fitness > 0 else None


def daily_loads(activities: list[tuple[date, dict[str, float | None]]]) -> dict[date, dict[str, float]]:
    """Sum activity loads into one total per day and ledger. Missing loads count as zero."""
    days: dict[date, dict[str, float]] = defaultdict(lambda: dict.fromkeys(LEDGERS, 0.0))
    for day, loads in activities:
        for ledger in LEDGERS:
            days[day][ledger] += loads.get(ledger) or 0.0
    return dict(days)


def segments(days: list[date], gap_days: int = GAP_DAYS) -> list[tuple[date, date]]:
    """Split activity days into runs with no gap longer than `gap_days`: (first, last) each."""
    days = sorted(days)
    out = [[days[0], days[0]]]
    for d in days[1:]:
        if (d - out[-1][1]).days > gap_days:
            out.append([d, d])
        else:
            out[-1][1] = d
    return [(a, b) for a, b in out]


def build(activities: list[tuple[date, dict[str, float | None]]], until: date | None = None) -> list[LedgerDay]:
    """Run all three ledgers from the first activity day through `until` (default today).

    A gap of more than GAP_DAYS with no activity is missing data: no days are written for it,
    and the ledgers start fresh after it, seeded the same way as the start of history. So months
    without synced data neither drag "your normal" toward zero nor draw a long decay on charts.
    """
    loads = daily_loads(activities)
    if not loads:
        return []
    spans = segments(list(loads))
    last = max(until or date.today(), max(loads))
    out: list[LedgerDay] = []
    for n, (start, end) in enumerate(spans):
        out.extend(_run(loads, start, last if n == len(spans) - 1 else end))
    return out


def _run(loads: dict[date, dict[str, float]], start: date, end: date) -> list[LedgerDay]:
    out: list[LedgerDay] = []
    seed_days = [start + timedelta(days=i) for i in range(min(FITNESS_DAYS, (end - start).days + 1))]
    for ledger in LEDGERS:
        # Seed both averages with the opening weeks' mean, so a history that starts mid-season
        # does not read as a huge ramp while the 42-day average warms up from zero.
        seed = sum(loads.get(d, {}).get(ledger, 0.0) for d in seed_days) / len(seed_days)
        history: list[LedgerDay] = []
        day = start
        while day <= end:
            history.append(_next(history, ledger, day, loads.get(day, {}).get(ledger, 0.0), seed))
            day += timedelta(days=1)
        out.extend(history)
    return out


def _next(history: list[LedgerDay], ledger: str, day: date, load: float, seed: float = 0.0) -> LedgerDay:
    fitness = history[-1].fitness if history else seed
    fatigue = history[-1].fatigue if history else seed
    today = LedgerDay(day, ledger, load, fitness + (load - fitness) / FITNESS_DAYS,
                      fatigue + (load - fatigue) / FATIGUE_DAYS)
    today.flags = _flags(today, history)
    return today


def extend(days: list[LedgerDay], until: date) -> list[LedgerDay]:
    """Carry stored ledgers forward through `until` as rest days, matching what `build` would
    write. `days` must hold at least the last FITNESS_DAYS days of the stored series, so the
    flags see the same history, and the current span must be at least FITNESS_DAYS old: before
    that, each new day still changes the opening-weeks seed (see `seeded`)."""
    span = current_span(days)
    out: list[LedgerDay] = []
    for ledger in LEDGERS:
        history = [d for d in span if d.ledger == ledger]
        if not history:
            continue
        day = history[-1].day + timedelta(days=1)
        while day <= until:
            history.append(_next(history, ledger, day, 0.0))
            out.append(history[-1])
            day += timedelta(days=1)
    return out


def seeded(days: list[LedgerDay]) -> bool:
    """Whether the current span is old enough that its seed no longer moves as days pass."""
    span = current_span(days)
    return bool(span) and (span[-1].day - span[0].day).days + 1 >= FITNESS_DAYS


def current_span(days: list[LedgerDay]) -> list[LedgerDay]:
    """The days since the last gap in a stored series (gap days are never stored)."""
    dates = sorted({d.day for d in days})
    start = dates[0] if dates else None
    for a, b in zip(dates, dates[1:]):
        if (b - a).days > 1:
            start = b
    return [d for d in days if d.day >= start] if start else []


def _flags(today: LedgerDay, history: list[LedgerDay]) -> list[str]:
    flags = []
    if len(history) + 1 < MIN_HISTORY_DAYS or today.fitness <= 0:
        return flags
    ratio = today.fatigue / today.fitness
    if ratio > OVERREACH_RATIO:
        flags.append("overreach")
    elif ratio > RAMP_RATIO:
        flags.append("ramp")
    recent_max = max((d.load for d in history[-FITNESS_DAYS:]), default=0.0)
    if recent_max > 0 and today.load > recent_max * SPIKE_RATIO:
        flags.append("spike")
    if len(history) >= DETRAINING_DAYS:
        before = history[-DETRAINING_DAYS].fitness
        if before > 0 and today.fitness < before * (1 - DETRAINING_DROP):
            flags.append("detraining")
    return flags


FLAG_TEXT = {
    "ramp": "Ramping too fast",
    "overreach": "Too much, back off",
    "spike": "Biggest day in 6 weeks by a wide margin",
    "detraining": "Fitness down over 10% in 2 weeks",
}


def describe_vs_normal(value: float | None) -> str:
    if value is None:
        return "no history yet"
    pct = round(value * 100)
    if pct == 0:
        return "at your normal"
    return f"{abs(pct)}% {'above' if pct > 0 else 'below'} your normal"


def times_usual_day(load: float, fitness_before: float) -> float | None:
    """An activity's load against a usual day, i.e. the fitness average going into it."""
    return load / fitness_before if fitness_before > 0 else None
