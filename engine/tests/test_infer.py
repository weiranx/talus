import numpy as np

from talus import cli
from talus.infer import best_average, drift, infer
from tests.conftest import make_streams

AET, ANT = 145, 165  # the hidden truth the synthetic runs are built around


def run(minutes, hr, speed=3.0, grade=0.0, sport="running", drift_frac=None):
    """A steady run at `hr`. Above AET heart rate drifts up 8% in the second half at the same
    pace; below it, 2%."""
    s = make_streams([(minutes * 60 * speed / 1609.344, grade, speed)])
    if drift_frac is None:
        drift_frac = 0.08 if hr > AET else 0.02
    t = s.time
    s.heart_rate = np.where(t < t[-1] / 2, hr, hr * (1 + drift_frac)).astype(float)
    s.sport = sport
    return s


def tempo(hr_easy=130, hr_hard=ANT):
    s = run(15 + 60 + 10, hr_easy)
    t = s.time
    s.heart_rate = np.where((t >= 15 * 60) & (t < 75 * 60), hr_hard, hr_easy).astype(float)
    return s


def history():
    return [(1, run(70, 135)), (2, run(70, 142)), (3, run(70, 150)), (4, run(70, 155)),
            (5, tempo()), (6, tempo(hr_hard=ANT - 2)),
            (7, run(60, 120, sport="bouldering")), (8, run(90, 175, sport="cycling"))]


def test_best_average_ignores_pauses():
    t = np.concatenate([np.arange(0, 1800), np.arange(5000, 6800)]).astype(float)
    hr = np.full(t.size, 150.0)
    hr[:1800] = 170
    assert best_average(hr, t, 1800) > 169.8
    assert best_average(hr, t, 7200) is None


def test_drift_reads_decoupling():
    hr, d, flat = drift(run(70, 150))
    assert d > 0.05 and flat > 55  # the warm-up is skipped, so it reads a little under 1 - 1/1.08
    assert drift(run(70, 140))[1] < 0.05
    assert drift(run(30, 150)) is None  # not enough steady time after warm-up
    assert drift(run(70, 140, grade=8)) is None  # hills confound pace
    assert drift(tempo()) is None  # a workout is not a steady run


def test_infer_from_a_mixed_history():
    r = infer(history())
    assert r["runs_with_hr"] == 6  # bouldering and cycling left out
    assert r["ant"].value == ANT and r["ant"].confidence == "medium" and {5, 6} <= set(r["ant"].activities)
    # Floor: held under 5% at 142. Ceiling: drifted at 150. The truth, 145, sits between.
    assert r["aet"].value == 142 and r["aet"].confidence == "high"
    assert "drifted at 15" in r["aet"].method
    assert r["aet"].activities[0] == 2
    assert r["zones"]["z2"].startswith(f"{r['aet'].value} to {ANT}")
    assert r["max"].value >= ANT


def test_aet_falls_back_to_88_percent_when_no_run_is_steady_and_flat():
    acts = [(1, tempo()), (2, run(70, 140, grade=8)), (3, tempo(hr_hard=ANT - 1))]
    r = infer(acts)
    assert r["aet"].confidence == "provisional" and r["aet"].value == round(ANT * 0.88)


def test_easy_only_history_flags_a_low_ant():
    r = infer([(1, run(70, 135)), (2, run(70, 138))])
    assert r["ant"].confidence == "low" or "likely reads low" in r["ant"].method
    assert r["aet"].confidence == "low"  # nothing drifted, so AeT is only a floor


def test_cli_infer_apply(tmp_path, monkeypatch, capsys):
    from tests.test_api import _gpx
    from datetime import date, datetime, timedelta
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    cli.main(["thresholds", "--resting", "54", "--ant", "187", "--max", "209"])
    files = []
    for k in range(3):
        d = datetime.combine(date.today() - timedelta(days=k + 1), datetime.min.time())
        f = tmp_path / f"r{k}.gpx"
        f.write_bytes(_gpx(d, n=4000, hr=150 + k))
        files.append(str(f))
    cli.main(["import", *files])
    capsys.readouterr()
    cli.main(["infer", "--apply"])
    out = capsys.readouterr().out
    assert "3 runs with heart rate" in out and "Saved:" in out
    from talus.store import Store
    th = Store(tmp_path / "data").thresholds()
    assert th.ant < 187 and th.max == 209 and th.resting == 54 and th.source.startswith("inferred")
