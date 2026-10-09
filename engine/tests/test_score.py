"""Synthetic versions of the spec's three sessions, scored end to end from streams."""

import pytest

from talus.score import score

from .conftest import make_streams


def test_mountain_run(athlete):
    # 13 mi, 4,500 ft up and down at 14% (the 8-20% band), run
    climb_mi = 4500 / 0.14 / 5280
    flat = (13 - 2 * climb_mi) / 2
    s = make_streams([(flat, 0, 3.0), (climb_mi, 14, 2.2), (climb_mi, -14, 3.2), (flat, 0, 3.0)])
    r = score(s, athlete)
    assert r.distance_mi == pytest.approx(13, rel=0.01)
    assert r.gain_ft == pytest.approx(4500, rel=0.01)
    assert r.loss_ft == pytest.approx(4500, rel=0.01)
    assert r.effort_mi == pytest.approx(21.5, abs=0.15)
    assert r.climb_load == pytest.approx(4500, rel=0.03)
    assert r.descent_load == pytest.approx(4500, rel=0.03)
    assert r.longest_climb_ft == pytest.approx(4500, rel=0.01)
    assert r.hike_share == 0
    kinds = [seg.kind for seg in r.segments]
    assert kinds == ["flat", "climb", "descent", "flat"]


def test_steep_peak_hike(athlete):
    # 6 mi, 3,000 ft up and down at 25%, hiked (low cadence)
    climb_mi = 3000 / 0.25 / 5280
    flat = (6 - 2 * climb_mi) / 2
    s = make_streams([(flat, 0, 1.6), (climb_mi, 25, 0.8), (climb_mi, -25, 1.1), (flat, 0, 1.6)],
                     cadence=110)
    r = score(s, athlete)
    assert r.gain_ft == pytest.approx(3000, rel=0.01)
    assert r.climb_load == pytest.approx(3750, rel=0.05)
    assert r.descent_load == pytest.approx(3150, rel=0.05)
    assert r.hike_share == pytest.approx(1)


def test_flat_run_with_noisy_gps_elevation(athlete):
    s = make_streams([(13, 0, 3.2)], barometric=False, noise_m=1.0)
    r = score(s, athlete)
    assert r.gain_ft < 100
    assert any("GPS-only" in n for n in r.notes)


def test_no_heart_rate_without_threshold_pace(athlete):
    r = score(make_streams([(5, 0, 3.0)], hr=None), athlete)
    assert r.cardio_load is None and not r.cardio_estimated


def test_no_heart_rate_estimates_from_pace(athlete):
    athlete.threshold_pace_s_per_mi = 420
    r = score(make_streams([(5, 0, 1609.344 / 420)], hr=None), athlete)
    assert r.cardio_estimated
    assert r.cardio_load == pytest.approx(5 * 420 / 3600 * 100, rel=0.02)
