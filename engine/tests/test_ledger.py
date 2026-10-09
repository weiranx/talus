from datetime import date, timedelta

import pytest

from talus import ledger

START = date(2026, 1, 1)


def days(loads_by_offset: dict[int, dict], n: int):
    """Activities on given day offsets; `n` sets how far the ledger runs."""
    acts = [(START + timedelta(days=i), loads) for i, loads in loads_by_offset.items()]
    return ledger.build(acts, until=START + timedelta(days=n - 1))


def series(result, name):
    return [d for d in result if d.ledger == name]


def test_steady_load_converges_to_its_own_normal():
    out = days({i: {"cardio": 80, "climb": 1500, "descent": 1500} for i in range(200)}, 200)
    last = series(out, "climb")[-1]
    assert last.fitness == pytest.approx(1500, rel=0.01)
    assert last.fatigue == pytest.approx(1500, rel=0.001)
    assert last.vs_normal == pytest.approx(0, abs=0.01)
    assert last.flags == []


def test_ledgers_are_separate():
    out = days({0: {"cardio": 100, "climb": 0, "descent": 5000}}, 1)
    by = {d.ledger: d for d in out}
    assert by["cardio"].load == 100 and by["climb"].load == 0 and by["descent"].load == 5000


def test_two_activities_same_day_add_up_and_missing_cardio_is_zero():
    acts = [(START, {"cardio": 50, "climb": 100, "descent": 100}),
            (START, {"cardio": None, "climb": 200, "descent": 300})]
    by = {d.ledger: d.load for d in ledger.build(acts, until=START)}
    assert by == {"cardio": 50, "climb": 300, "descent": 400}


def test_fitness_and_fatigue_time_constants():
    steady = {i: {"climb": 1000} for i in range(60)}
    out = series(days(steady | {60: {"climb": 4200}}, 61), "climb")
    assert out[60].fitness - out[59].fitness == pytest.approx((4200 - out[59].fitness) / 42)
    assert out[60].fatigue - out[59].fatigue == pytest.approx((4200 - out[59].fatigue) / 7)


def test_history_starting_mid_season_does_not_flag_a_ramp():
    out = days({i: {"cardio": 80, "climb": 1500, "descent": 1500} for i in range(45)}, 45)
    assert all(not d.flags for d in out)


def test_descent_block_trips_ramp_then_overreach_without_touching_cardio():
    base = {i: {"cardio": 80, "climb": 1000, "descent": 1000} for i in range(60)}
    block = {i: {"cardio": 80, "climb": 1000, "descent": 3000} for i in range(60, 67)}
    out = days(base | block, 67)
    descent_flags = {f for d in series(out, "descent")[60:] for f in d.flags}
    assert {"ramp", "overreach"} & descent_flags
    assert all(not d.flags for d in series(out, "cardio")[60:])


def test_spike():
    base = {i: {"descent": 1000} for i in range(50)}
    out = series(days(base | {50: {"descent": 1400}}, 51), "descent")
    assert "spike" in out[50].flags
    out = series(days(base | {50: {"descent": 1250}}, 51), "descent")
    assert "spike" not in out[50].flags


def test_detraining_after_two_weeks_off():
    out = series(days({i: {"climb": 1000} for i in range(60)}, 75), "climb")
    assert "detraining" in out[-1].flags


def test_no_flags_during_warmup():
    out = days({i: {"descent": 1000 * (i + 1)} for i in range(20)}, 20)
    assert all(not d.flags for d in out)


def test_plain_language():
    assert ledger.describe_vs_normal(0.4) == "40% above your normal"
    assert ledger.describe_vs_normal(-0.25) == "25% below your normal"
    assert ledger.describe_vs_normal(None) == "no history yet"
    assert ledger.times_usual_day(3300, 1000) == pytest.approx(3.3)
    assert ledger.times_usual_day(100, 0) is None


def _steady(start, days, every=1, load=100.0):
    return [(start + timedelta(days=k), {"cardio": load, "climb": load * 10, "descent": load * 8})
            for k in range(0, days, every)]


def test_long_gap_is_missing_data_not_rest():
    from talus import ledger
    spring = _steady(date(2026, 3, 1), 40)
    autumn = _steady(date(2026, 9, 1), 39)
    days = ledger.build(spring + autumn, until=date(2026, 10, 9))
    assert not [d for d in days if date(2026, 4, 10) < d.day < date(2026, 9, 1)]  # no rows for the gap
    last = [d for d in days if d.ledger == "cardio"][-1]
    # Same load every day after the gap: normal is that load, not dragged toward zero by summer.
    assert abs(last.fitness - 100) < 1 and abs(last.vs_normal) < 0.01 and not last.flags
    assert ledger.segments([d for d, _ in spring + autumn]) == [
        (date(2026, 3, 1), date(2026, 4, 9)), (date(2026, 9, 1), date(2026, 10, 9))]


def test_a_rest_fortnight_still_counts_as_rest():
    from talus import ledger
    acts = _steady(date(2026, 8, 1), 40) + _steady(date(2026, 9, 24), 10)  # 14 days off between
    days = [d for d in ledger.build(acts, until=date(2026, 10, 3)) if d.ledger == "cardio"]
    assert len(ledger.segments([d for d, _ in acts])) == 1
    assert len(days) == (date(2026, 10, 3) - date(2026, 8, 1)).days + 1  # every day kept
    rest_end = [d for d in days if d.day == date(2026, 9, 23)][0]
    assert rest_end.fitness < 80  # the fortnight off lowered fitness, as rest should


def test_current_span_starts_after_the_last_gap():
    from talus import ledger
    days = ledger.build(_steady(date(2026, 3, 1), 10) + _steady(date(2026, 9, 1), 5), until=date(2026, 9, 10))
    span = ledger.current_span(days)
    assert min(d.day for d in span) == date(2026, 9, 1)
