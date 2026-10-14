"""Contract test against the real strava.com, with your own session. Off unless asked for:

    STRAVA_LIVE_TESTS=1 STRAVA_SESSION_COOKIE='...' uv run pytest tests/test_strava_web_live.py

STRAVA_SESSION_COOKIE is the Cookie header from a browser signed in to Strava (see
`talus strava-web setup` for where to copy it). STRAVA_LIVE_ACTIVITY_ID, one of your own
activities, also checks export_original. Paced like a sync, a request a second; a captcha or an
ended session fails the test rather than being retried.
"""

import os
from datetime import date, timedelta

import pytest

from talus.config import load_env
from talus.sources.base import RemoteActivity
from talus.sources.strava_web import puller

pytestmark = pytest.mark.skipif(os.environ.get("STRAVA_LIVE_TESTS") != "1", reason="set STRAVA_LIVE_TESTS=1")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    load_env()
    if not os.environ.get("STRAVA_SESSION_COOKIE"):
        pytest.skip("set STRAVA_SESSION_COOKIE")
    c = puller.open_client(tmp_path_factory.mktemp("strava"))
    yield c
    c.close()


def test_signed_in_pages_carry_a_csrf_token(client):
    assert client.get("/dashboard").status_code == 200
    assert client.csrf, "no csrf-token meta on a signed-in page"


def test_the_activity_list_has_what_a_sync_reads(client):
    page = puller.list_page(client, 1)
    assert page["perPage"] == 20 and page["models"]
    for key in ("id", "start_date_local_raw", "start_time", "distance_raw", "elevation_gain_raw", "sport_type"):
        assert key in page["models"][0], key
    acts = puller.StravaWebSource(client).list_activities(date.today() - timedelta(days=30))
    assert all(a.source_id and a.start_time for a in acts)
    assert [a.start_time for a in acts] == sorted((a.start_time for a in acts), reverse=True)


def test_export_original_is_an_activity_file(client):
    activity_id = os.environ.get("STRAVA_LIVE_ACTIVITY_ID")
    if not activity_id:
        pytest.skip("set STRAVA_LIVE_ACTIVITY_ID to one of your activities")
    raw = puller.StravaWebSource(client).fetch(RemoteActivity("strava_web", activity_id, None))
    assert raw is not None and raw.fmt in ("fit", "gpx", "tcx")
