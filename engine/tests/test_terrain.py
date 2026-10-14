from __future__ import annotations

import numpy as np

from talus import terrain
from tests.conftest import make_streams


def _hiking(s, below_mps=1.5):
    speed = np.diff(s.distance, prepend=s.distance[0])
    return speed < below_mps


def test_grade_cost_is_one_on_the_flat_dips_on_gentle_downhill_and_climbs_steeply():
    c = terrain.grade_cost(np.array([0.0, 0.1, 0.3, -0.1, -0.2, -0.35]))
    assert c[0] == 1.0
    assert 1.3 < c[1] < 1.45 and 2.0 < c[2] < 2.5
    assert 0.85 < c[3] < 0.95
    assert 0.95 < c[4] < 1.05 and c[5] > 1.3


def test_bands_rate_climbs_and_descents_in_feet_per_hour():
    # 1 mi up at 12% at 1.2 m/s (hiked), 1 mi down at 12% at 3 m/s (run).
    s = make_streams([(1.0, 12, 1.2), (1.0, -12, 3.0)])
    t = terrain.analyze(s, _hiking(s))
    bands = {b["key"]: b for b in t["bands"]}
    up, down = bands["c8"], bands["d8"]
    # 1.2 m/s at 12% climbs 0.144 m/s: 1,701 ft/h.
    assert abs(up["vert_rate_ft_h"] - 1701) < 80
    assert up["hike_share"] > 0.9 and up["run"] is None
    assert abs(down["vert_rate_ft_h"] - 4252) < 200
    assert down["pace_min_mi"] and 8.5 < down["pace_min_mi"] < 9.5
    assert up["avg_hr"] == 140.0


def test_short_bands_are_left_out_and_no_altitude_gives_none():
    s = make_streams([(0.05, 20, 1.0), (2.0, 0, 3.0)])
    t = terrain.analyze(s, _hiking(s))
    assert all(b["key"] != "c15" for b in t["bands"])
    s.altitude = None
    assert terrain.analyze(s, _hiking(s)) is None


def test_durability_is_fair_to_an_out_and_back():
    # Climb out slowly, run home fast at the same heart rate: grade-adjusted pace barely changes,
    # where raw pace would show a big negative split.
    s = make_streams([(3.5, 8, 2.2), (3.5, -8, 3.6)])
    d = terrain.analyze(s, _hiking(s, 0))["durability"]
    assert d is not None
    assert abs(d["pace_change_pct"]) < 20
    assert d["verdict"] == "held"
    assert d["first"]["gain_ft"] > 3 * d["second"]["gain_ft"]


def test_durability_sees_heart_rate_drift():
    s = make_streams([(8.0, 0, 3.0)])
    s.heart_rate = np.linspace(135, 160, s.time.size)
    d = terrain.analyze(s, _hiking(s, 0))["durability"]
    assert d["decoupling_pct"] > 5
    assert d["verdict"] in ("drifted", "faded")
    assert terrain.analyze(make_streams([(2.0, 0, 3.0)]), np.zeros(1, bool))["durability"] is None


def test_compare_against_usual_and_hike_or_run():
    def band(rate, hr=140, hike=None, run=None):
        return {"key": "c15", "label": "15 to 25%", "kind": "climb", "time_s": 600, "distance_mi": 0.5,
                "vert_ft": 500, "vert_rate_ft_h": rate, "pace_min_mi": 30, "hike_share": 0.5, "avg_hr": hr,
                "hike": hike, "run": run}

    this = {"bands": [band(2000, 150, hike={"vert_rate_ft_h": 1800, "avg_hr": 140, "time_s": 300, "pace_min_mi": 30},
                           run={"vert_rate_ft_h": 1900, "avg_hr": 160, "time_s": 300, "pace_min_mi": 28})],
            "durability": None}
    history = [{"bands": [band(r)], "durability": None} for r in (1500, 1600, 1700)] + [None]
    out = terrain.compare(this, history)
    u = out["bands"][0]["usual"]
    assert u["vert_rate_ft_h"] == 1600 and u["rate_change_pct"] == 25 and u["fastest"]
    assert u["hr_change_bpm"] == 10
    assert out["hike_or_run"][0]["run_rate_gain_pct"] == 6
    assert out["hike_or_run"][0]["run_hr_cost_bpm"] == 20
    # Fewer than three earlier activities on a grade: no usual.
    assert terrain.compare(this, history[:2])["bands"][0]["usual"] is None
    assert terrain.compare(None, history) is None
