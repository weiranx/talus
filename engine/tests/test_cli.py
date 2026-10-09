from datetime import date

from talus import cli, ledger
from talus.config import data_dir
from talus.store import Store

from .test_parse import _gpx


def test_load_extends_stale_ledgers_and_unscored_cardio_is_na(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    with_hr = tmp_path / "with_hr.gpx"
    with_hr.write_bytes(_gpx(600))
    no_hr = tmp_path / "no_hr.gpx"
    no_hr.write_bytes(_gpx(600).replace(b"<gpxtpx:hr>150</gpxtpx:hr>", b"")
                      .replace(b"2026-10-01", b"2026-10-02"))
    cli.main(["thresholds", "--resting", "45", "--ant", "172", "--max", "190"])
    cli.main(["import", str(with_hr), str(no_hr)])

    # Ledgers last built on an earlier day, then rest days pass with no new activity.
    store = Store(data_dir())
    store.save_ledgers(ledger.build(store.scored_loads(), until=date(2026, 10, 2)))
    capsys.readouterr()

    cli.main(["load"])
    assert f"Ledgers on {date.today()}" in capsys.readouterr().out

    cli.main(["score", "2"])
    out = capsys.readouterr().out
    assert "cardio not scored" in out
    assert "cardio n/a" in out
