"""Pull the owner's own activities from strava.com's website (see client.py for why and when).

Each activity Strava lists is matched against what Talus already has before anything is
downloaded. A close match (start within ALIAS_START_S, distance within ALIAS_DISTANCE) keeps
the stored record, preferring one kept as an original FIT, and files the Strava id as its alias.
Anything else gets Strava's original upload (export_original: FIT, gzipped FIT, TCX or GPX), kept
under raw/strava_web/ and scored like a file from any other source.

Strava's own numbers (Relative Effort, Fitness and Freshness) go to external_metrics with
source 'strava', for display and for checking Talus's loads. The coach never reads them.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from ..base import RawFile, RemoteActivity, sniff_format, unwrap
from .client import StravaWebClient, StravaWebError, cookie_path

SOURCE = "strava_web"
METRICS_SOURCE = "strava"
ALIAS_START_S = 60
ALIAS_DISTANCE = 0.01
LOCK_FILE = "strava_web.lock"


class StravaNotMapped(StravaWebError):
    """A step that needs an endpoint read from a recorded session first."""
    code = "not_mapped"


class StravaWebSource:
    name = SOURCE

    def __init__(self, client: StravaWebClient):
        self.client = client

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]:
        # TODO(strava-web): map the activity-list endpoint from reverse-engineering/activity-read.har,
        # which isn't in the repo yet. Each RemoteActivity needs the Strava id, local start time and
        # distance (for matching before download), plus name and sport. Not guessed on purpose.
        raise StravaNotMapped("Strava's activity list isn't mapped yet: add reverse-engineering/activity-read.har.")

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
        # TODO(strava-web): Relative Effort and Fitness/Freshness, once the HAR shows which requests
        # carry them. Keys to use: relative_effort, fitness, freshness.
        return {}


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


def sync(store, since: date, log=print, source: StravaWebSource | None = None) -> dict:
    """Pull new Strava activities since a day into this account's store, score them and rebuild
    the ledgers. The outcome is kept as last_sync_strava_web; it never becomes the account's
    last_sync, which belongs to the sources people see."""
    from ...cli import _score_and_save, rebuild_ledgers

    result = {"at": datetime.now().isoformat(timespec="seconds"), "source": SOURCE, "since": since.isoformat(),
              "listed": 0, "new": 0, "saved": 0, "aliased": 0, "duplicates": 0, "error": None, "error_code": None}
    added: list[int] = []
    with _one_at_a_time(store.dir) as mine:
        if not mine:
            log("Strava: another sync is running for this account; skipped.")
            return result | {"added_ids": added, "skipped": True}
        try:
            source = source or StravaWebSource(StravaWebClient.from_env(cookie_path(store.dir)))
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
