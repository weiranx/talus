"""Pull the owner's own activities from strava.com's website (see client.py for why and when).

Each activity Strava lists is matched against what Talus already has before anything is
downloaded. A close match (start within ALIAS_START_S, distance within ALIAS_DISTANCE) keeps
the stored record, preferring one kept as an original FIT, and files the Strava id as its alias.
Anything else gets Strava's original upload (export_original: FIT, gzipped FIT, TCX or GPX), kept
under raw/strava_web/ and scored like a file from any other source.

Strava's own numbers go to external_metrics with source 'strava', for display and for checking
Talus's loads. The coach never reads them. Both are for Strava subscribers only: Relative Effort
is the list's suffer_score, null without a subscription; Fitness & Freshness isn't mapped.

Endpoints are in reverse-engineering/strava-web.md.
"""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from ..base import RawFile, RemoteActivity, local_naive, sniff_format, unwrap
from .client import StravaWebClient, StravaWebError, cookie_path

SOURCE = "strava_web"
METRICS_SOURCE = "strava"
ALIAS_START_S = 60
ALIAS_DISTANCE = 0.01
LOCK_FILE = "strava_web.lock"

# The request the My Activities page (/athlete/training) makes for its later pages. Strava
# returns 20 a page whatever per_page asks for, newest first.
LIST_PATH = "/athlete/training_activities"
LIST_QUERY = {"keywords": "", "sport_type": "", "tags": "", "commute": "", "private_activities": "",
              "trainer": "", "gear": "", "new_activity_only": "false", "per_page": "20"}
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "text/javascript, application/javascript"}
MAX_PAGES = 200  # 4,000 activities: a sync's window never reaches that far


def list_page(client: StravaWebClient, page: int) -> dict:
    """One page of My Activities: {"models": [...], "page", "perPage", "total"}."""
    resp = client.get(LIST_PATH, params={**LIST_QUERY, "page": str(page)}, headers=XHR)
    if resp.status_code != 200:
        # Without a live session this endpoint answers 500 rather than sending you to /login.
        raise StravaWebError(f"Strava's activity list answered {resp.status_code}. If it keeps doing so, "
                             "the session may have ended: run `talus strava-web setup` again.")
    try:
        body = resp.json()
    except ValueError:
        raise StravaWebError("Strava's activity list wasn't JSON; the website may have changed.") from None
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise StravaWebError("Strava's activity list had no models; the website may have changed.")
    return body


def to_activity(m: dict) -> RemoteActivity:
    """A list entry. start_date_local_raw is the local wall-clock start written as epoch seconds
    read as UTC, so read back as UTC it is the local start Talus files a day under."""
    if m.get("start_date_local_raw") is not None:
        start = datetime.fromtimestamp(m["start_date_local_raw"], timezone.utc).replace(tzinfo=None)
    else:
        start = local_naive(datetime.strptime(m["start_time"], "%Y-%m-%dT%H:%M:%S%z"))
    return RemoteActivity(source=SOURCE, source_id=str(m["id"]), start_time=start, name=m.get("name"),
                          sport=m.get("sport_type"), distance_m=m.get("distance_raw"),
                          gain_m=m.get("elevation_gain_raw"), raw=m)


class StravaWebSource:
    name = SOURCE

    def __init__(self, client: StravaWebClient):
        self.client = client

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]:
        """Every activity from `since` to `until`, paging back from the newest."""
        out = []
        for page in range(1, MAX_PAGES + 1):
            body = list_page(self.client, page)
            for m in body["models"]:
                act = to_activity(m)
                if act.start_time.date() < since:
                    return out
                if until is None or act.start_time.date() <= until:
                    out.append(act)
            per_page = body.get("perPage") or len(body["models"])
            if not body["models"] or len(body["models"]) < per_page or page * per_page >= (body.get("total") or 0):
                break
        return out

    def fetch(self, activity: RemoteActivity) -> RawFile | None:
        """The file as it was uploaded to Strava: FIT (maybe gzipped), TCX or GPX."""
        resp = self.client.get(f"/activities/{activity.source_id}/export_original")
        if resp.status_code != 200 or not resp.content:
            return None
        data = unwrap(resp.content)
        try:
            return RawFile(data, sniff_format(data))
        except ValueError:
            return None

    def metrics(self, activity: RemoteActivity) -> dict[str, float | None]:
        """Relative Effort, when Strava gives it (subscribers only). Fitness & Freshness is a
        subscriber page too and isn't mapped: no unsubscribed account can see its request."""
        effort = activity.raw.get("suffer_score")
        return {"relative_effort": float(effort)} if effort is not None else {}


def open_client(folder: Path, saved_cookie: str | None = None, cookie_file: Path | None = None,
                session_cookie: str | None = None, **kwargs) -> StravaWebClient:
    """The account's Strava session: STRAVA_SESSION_COOKIE from .env (or `session_cookie`) wins,
    then the jar on disk, then the cookie setup saved."""
    return StravaWebClient(cookie_file or cookie_path(folder),
                           session_cookie=session_cookie or os.environ.get("STRAVA_SESSION_COOKIE"),
                           saved_cookie=saved_cookie, **kwargs)


@contextmanager
def _one_at_a_time(folder: Path):
    """One Strava sync per account across the server and the CLI; a second one skips."""
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / LOCK_FILE, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def sync(store, since: date, log=print, source: StravaWebSource | None = None,
         saved_cookie: str | None = None) -> dict:
    """Pull new Strava activities since a day into this account's store, score them and rebuild
    the ledgers. `saved_cookie` is the one setup saved. The outcome is kept as
    last_sync_strava_web; it never becomes the account's last_sync, which belongs to the sources
    people see."""
    from ...cli import _score_and_save, rebuild_ledgers

    result = {"at": datetime.now().isoformat(timespec="seconds"), "source": SOURCE, "since": since.isoformat(),
              "listed": 0, "new": 0, "saved": 0, "aliased": 0, "duplicates": 0, "error": None, "error_code": None}
    added: list[int] = []
    with _one_at_a_time(store.dir) as mine:
        if not mine:
            log("Strava: another sync is running for this account; skipped.")
            return result | {"added_ids": added, "skipped": True}
        try:
            source = source or StravaWebSource(open_client(store.dir, saved_cookie))
            remote = sorted(source.list_activities(since), key=lambda a: a.start_time)
            result["listed"] = len(remote)
            th = store.thresholds()
            for act in remote:
                if store.has(SOURCE, act.source_id):
                    continue
                target = store.alias_target(act.start_time, act.distance_m, ALIAS_START_S, ALIAS_DISTANCE)
                if target is not None:
                    store.add_alias(target, SOURCE, act.source_id)
                    _keep_metrics(store, source, act, target)
                    result["aliased"] += 1
                    continue
                if store.duplicate_of(act.start_time, act.distance_m) is not None:
                    # Close, but not close enough to call it the same record: never count it twice.
                    log(f"  skip {act.start_time:%Y-%m-%d} {act.name}: near an activity already here")
                    result["duplicates"] += 1
                    continue
                result["new"] += 1
                raw = source.fetch(act)
                if raw is None:
                    log(f"  skip {act.start_time:%Y-%m-%d} {act.name}: no original file")
                    continue
                activity_id = store.add(act, raw)
                added.append(activity_id)
                result["saved"] += 1
                _keep_metrics(store, source, act, activity_id)
                _score_and_save(store, activity_id, raw, th, act.sport,
                                label=f"{act.start_time:%Y-%m-%d} {act.name}", log=log)
            log(f"Strava: {result['listed']} listed, {result['saved']} new, {result['aliased']} already here")
            if added and th is not None:
                rebuild_ledgers(store)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            result["error_code"] = getattr(e, "code", "error")
            raise
        finally:
            store.save_settings({f"last_sync_{SOURCE}": result})
            if source is not None and hasattr(source.client, "close"):
                source.client.close()
    return result | {"added_ids": added}


def _keep_metrics(store, source: StravaWebSource, act: RemoteActivity, activity_id: int) -> None:
    values = source.metrics(act)
    if values:
        store.save_external_metrics(activity_id, METRICS_SOURCE, values)
