import numpy as np
import pytest

from talus import elevation


def test_noise_on_flat_is_not_vert():
    alt = 1000 + np.random.default_rng(1).normal(0, 0.5, 5000)
    gain, loss, _, _ = elevation.hysteresis(elevation.median_filter(alt), elevation.BARO_HYSTERESIS_M)
    assert gain.sum() < 3 and loss.sum() < 3


def test_clean_climb_and_descent_are_exact():
    alt = np.concatenate([np.linspace(1000, 1500, 500), np.linspace(1500, 1100, 400)])
    gain, loss, climbs, descents = elevation.hysteresis(alt, 3.0)
    assert gain.sum() == pytest.approx(500)
    assert loss.sum() == pytest.approx(400)
    assert climbs == [pytest.approx(500)] and descents == [pytest.approx(400)]


def test_small_dip_does_not_end_a_long_climb():
    alt = np.concatenate([np.linspace(1000, 1300, 300), np.linspace(1300, 1290, 10),
                          np.linspace(1290, 1600, 310)])
    _, _, climbs, _ = elevation.hysteresis(alt, elevation.LONG_CLIMB_HYSTERESIS_M)
    assert max(climbs) == pytest.approx(600)


def test_median_filter_removes_spike():
    alt = np.full(50, 1000.0)
    alt[25] = 1100
    assert elevation.median_filter(alt).max() == 1000


def test_smoothed_grade():
    d = np.arange(0, 1000, 1.0)
    alt = d * 0.12
    g = elevation.smoothed_grade(d, alt)
    assert g[500] == pytest.approx(12)
