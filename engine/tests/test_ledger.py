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
