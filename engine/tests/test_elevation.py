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


# Glitches seen in real files. A flat-ish 9.4 mi run has about 124 ft up; each glitch alone
# used to inflate that to thousands or hundreds of thousands of feet.
def _san_diego_run(**kw):
    from tests.conftest import make_streams
    return make_streams([(4.7, 0.5, 3.0), (4.7, -0.5, 3.0)], **kw)


def _gain(s, athlete):
    from talus.score import score
    return score(s, athlete)


def _glitched(mutate, athlete, **kw):
    s = _san_diego_run(**kw)
    s.altitude = mutate(s.altitude.copy())
    return _gain(s, athlete)


def test_spike_bursts_longer_than_the_median_window(athlete):
    rng = np.random.default_rng(1)

    def bursts(a):
        for i in range(50, a.size, 120):
            a[i:i + 6] += rng.choice([-1, 1]) * rng.uniform(50, 300)
        return a
    r = _glitched(bursts, athlete)
    assert 100 < r.gain_ft < 160
    assert any("glitches" in n for n in r.notes)


def test_dropouts_to_zero_and_offset_steps(athlete):
    rng = np.random.default_rng(2)

    def dropouts(a):
        a[rng.random(a.size) < 0.1] = 0.0
        return a

    def step(a):
        a[a.size // 3:] += 500
        return a
    assert 100 < _glitched(dropouts, athlete).gain_ft < 160
    assert 100 < _glitched(step, athlete).gain_ft < 160


def test_two_interleaved_altitude_sources(athlete):
    def interleave(a):
        a[::2] += 60
        return a
    assert _glitched(interleave, athlete).gain_ft < 300


def test_gps_noise_in_a_file_that_claims_a_barometer(athlete):
    rng = np.random.default_rng(3)
    r = _glitched(lambda a: a + rng.normal(0, 8, a.size), athlete, barometric=True)
    assert r.gain_ft < 300
    assert any("noisier than a barometer" in n for n in r.notes)


def test_real_steep_terrain_is_untouched(athlete):
    from tests.conftest import make_streams
    # 1.5 mi at 30% up, then 1.5 mi at 30% down, run fast: about 2,376 ft each way.
    s = make_streams([(1.5, 30, 1.2), (1.5, -30, 3.5)])
    r = _gain(s, athlete)
    assert abs(r.gain_ft - 2376) < 30 and abs(r.loss_ft - 2376) < 30
    assert not any("glitch" in n or "possible" in n for n in r.notes)


def test_impossible_gain_rate_is_called_out(athlete):
    from tests.conftest import make_streams
    # 2 m of climb every second for half an hour passes the glitch filter but no one can do it.
    s = make_streams([(2.0, 66, 3.0)])
    r = _gain(s, athlete)
    assert any("not humanly possible" in n for n in r.notes)
