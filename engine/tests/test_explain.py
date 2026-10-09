from datetime import date, datetime, timedelta

import pytest

from talus import cli, explain, ledger, loads
from tests.conftest import sign_in
from tests.test_api import _gpx


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path / "data"))
    files = []
    for k in range(30, 0, -1):
        d = datetime.combine(date.today() - timedelta(days=k), datetime.min.time()) + timedelta(hours=14)
        f = tmp_path / f"run{k:02d}.gpx"
        f.write_bytes(_gpx(d, climb_per_s=1.0 if k == 1 else 0.3))
        files.append(str(f))
    cli.main(["thresholds", "--resting", "45", "--aet", "150", "--ant", "172", "--max", "190"])
    cli.main(["import", *files])
    return sign_in()


def _num(text: str) -> float:
    return float(text.split()[0].replace(",", ""))


def test_ledger_numbers_show_the_rolling_average_that_made_them(client):
    t = client.get("/api/today").json()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for name in ledger.LEDGERS:
        d = t["ledgers"][name]
        fat = client.get(f"/api/explain/ledger/{name}/fatigue").json()
        assert abs(fat["value"] - d["fatigue"]) < 1e-9 and "÷ 7" in fat["formula"]
        prev, load = _num(fat["inputs"][0]["value"]), _num(fat["inputs"][1]["value"])
        assert abs(prev + (load - prev) / 7 - d["fatigue"]) < 0.1
        fit = client.get(f"/api/explain/ledger/{name}/fitness").json()
        assert abs(fit["value"] - d["fitness"]) < 1e-9 and "÷ 42" in fit["formula"]
        vs = client.get(f"/api/explain/ledger/{name}/vs_normal").json()
        assert vs["steps"][-1].endswith(d["vs_normal_text"])
        past = client.get(f"/api/explain/ledger/{name}/load?day={yesterday}").json()
        assert len(past["inputs"]) == 1 and abs(_num(past["inputs"][0]["value"]) - past["value"]) < 1
    assert client.get("/api/explain/ledger/legs/fatigue").status_code == 404
    assert client.get("/api/explain/ledger/climb/form").status_code == 404


def test_activity_numbers_add_up(client):
    big = client.get("/api/activities").json()[0]
    a = client.get(f"/api/activities/{big['id']}").json()
    get = lambda m: client.get(f"/api/explain/activity/{big['id']}/{m}").json()

    climb = get("climb_load")
    total = sum(_num(i["value"]) * loads.CLIMB_FACTOR[b] for i, b in zip(climb["inputs"], loads.BANDS))
    assert abs(total - a["loads"]["climb"]["load"]) < 3 and abs(climb["value"] - big["climb_load"]) < 1e-6

    descent = get("descent_load")
    terms = [float(x.replace(",", "")) for x in descent["steps"][0].split(" = ")[0].split(" + ")]
    assert abs(sum(terms) - big["descent_load"]) < len(terms)  # each term rounds to the foot

    eff = get("effort_mi")
    assert abs(eff["value"] - big["effort_mi"]) < 1e-9 and "528" in eff["formula"]

    cardio = get("cardio_load")
    trimp, per_hour = (float(x.split()[-1].replace(",", "")) for x in cardio["steps"][0].split(", one hour at AnT = "))
    assert abs(trimp / per_hour * 100 - big["cardio_load"]) < 1

    usual = get("times_usual_descent")
    assert abs(usual["value"] - a["loads"]["descent"]["times_usual_day"]) < 1e-9
    assert client.get(f"/api/explain/activity/{big['id']}/pace").status_code == 404
    assert client.get("/api/explain/activity/999/climb_load").status_code == 404


def test_explanations_use_miles_and_feet():
    assert all("km" not in v and " m " not in v for v in explain.LEDGER_UNIT.values())
