"""The spec's worked example, scored from its grade-band splits."""

import numpy as np
import pytest

from talus import loads
from talus.models import Thresholds


@pytest.mark.parametrize("miles, gain_ft, expected", [(13, 300, 13.6), (13, 4500, 21.5), (6, 3000, 11.7)])
def test_effort_miles(miles, gain_ft, expected):
    assert round(loads.effort_miles(miles, gain_ft), 1) == expected


def test_flat_run_vert_loads():
    # 300 ft up and down, all under 8%
    assert loads.climb_load({"<8": 300}) == pytest.approx(225)
    assert loads.descent_load({"<8": 300}) == pytest.approx(150)


def test_mountain_run_vert_loads():
    # 4,500 ft up and down, run; one band split that reproduces the spec's figures
    assert loads.climb_load({"8-20": 3000, ">20": 1500}) == pytest.approx(4875)
    assert loads.descent_load({"8-20": 3500, ">20": 1000}) == pytest.approx(5000)


def test_peak_hike_vert_loads():
    # 3,000 ft up and down, all over 20%, hiked down
    assert loads.climb_load({">20": 3000}) == pytest.approx(3750)
    assert loads.descent_load({">20": 3000}, hiking=True) == pytest.approx(3150)


def test_descent_gap_between_flat_run_and_peak_hike():
    """The spec's headline: similar cardio, descent loads 21 times apart."""
    assert loads.descent_load({">20": 3000}, hiking=True) / loads.descent_load({"<8": 300}) == pytest.approx(21)


def test_one_hour_at_ant_is_100(athlete):
    hr = np.full(3600, athlete.ant)
    assert loads.cardio_load(hr, np.ones(3600), athlete) == pytest.approx(100)


def test_female_exponent_also_scales_to_100():
    th = Thresholds(resting=50, aet=148, ant=168, max=185, sex="female")
    assert loads.cardio_load(np.full(3600, 168.0), np.ones(3600), th) == pytest.approx(100)


def test_easy_flat_run_matches_spec_cardio(athlete):
    # 1:50 at a steady 142 bpm, just under AeT, lands on the spec's 94
    assert loads.cardio_load(np.full(6600, 142.0), np.ones(6600), athlete) == pytest.approx(94, abs=1.5)


def test_zone_minutes(athlete):
    hr = np.array([140.0] * 60 + [160.0] * 120 + [180.0] * 30)
    z = loads.zone_minutes(hr, np.ones(hr.size), athlete)
    assert z == {"z1": 1.0, "z2": 2.0, "z3": 0.5}


def test_estimated_cardio_flat_at_threshold_pace_is_100():
    speed = 1609.344 / 420  # 7:00 per mile
    load = loads.estimated_cardio_load(np.full(3600, speed), np.zeros(3600), np.ones(3600), 420)
    assert load == pytest.approx(100)
