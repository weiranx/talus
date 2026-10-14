"""Contract test against the real strava.com, with your own sign-in. Off unless asked for:

    STRAVA_LIVE_TESTS=1 STRAVA_EMAIL=... STRAVA_PASSWORD=... uv run pytest tests/test_strava_web_live.py

STRAVA_SESSION_COOKIE works instead of the password. STRAVA_LIVE_ACTIVITY_ID, one of your own
activities, also checks export_original. Paced like a sync (a request a second); a captcha fails
the test rather than being retried.
"""

import os
from datetime import date, timedelta

import pytest

from talus.config import load_env
from talus.sources.base import RemoteActivity
from talus.sources.strava_web.client import StravaWebClient, cookie_path
from talus.sources.strava_web.puller import StravaNotMapped, StravaWebSource

pytestmark = pytest.mark.skipif(os.environ.get("STRAVA_LIVE_TESTS") != "1", reason="set STRAVA_LIVE_TESTS=1")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    load_env()
    c = StravaWebClient.from_env(cookie_path(tmp_path_factory.mktemp("strava")))
    yield c
    c.close()


def test_signs_in_and_holds_a_session(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200, resp.status_code
    assert client.csrf, "no csrf-token meta on a signed-in page"


def test_export_original_is_an_activity_file(client):
    activity_id = os.environ.get("STRAVA_LIVE_ACTIVITY_ID")
    if not activity_id:
        pytest.skip("set STRAVA_LIVE_ACTIVITY_ID to one of your activities")
    raw = StravaWebSource(client).fetch(RemoteActivity("strava_web", activity_id, None))
    assert raw is not None and raw.fmt in ("fit", "gpx", "tcx")


@pytest.mark.xfail(raises=StravaNotMapped, reason="activity list waits on reverse-engineering/activity-read.har")
def test_lists_recent_activities(client):
    acts = StravaWebSource(client).list_activities(date.today() - timedelta(days=30))
    assert all(a.source_id and a.start_time for a in acts)
