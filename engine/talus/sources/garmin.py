"""Garmin Connect via the unofficial python-garminconnect library. Single user only.

Reuses saved tokens (the same ~/.garminconnect store the garminstravamcp server uses), so MFA
only happens once. Install with: uv sync --extra garmin
"""

from __future__ import annotations

import os
from datetime import date, datetime

from .base import RawFile, RemoteActivity, sniff_format, unwrap


class GarminSource:
    name = "garmin"

    def __init__(self, tokenstore: str | None = None):
        from garminconnect import Garmin

        self._fmt = Garmin.ActivityDownloadFormat.ORIGINAL
        store = os.path.expanduser(tokenstore or os.environ.get("GARMIN_TOKENSTORE", "~/.garminconnect"))
        try:
            self.api = Garmin()
            self.api.login(store)
        except Exception:
            self.api = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
            self.api.login()
            self.api.garth.dump(store)

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]:
        rows = self.api.get_activities_by_date(since.isoformat(), (until or date.today()).isoformat())
        return [RemoteActivity(
            source=self.name,
            source_id=str(a["activityId"]),
            start_time=datetime.fromisoformat(a["startTimeLocal"]),
            name=a.get("activityName"),
            sport=(a.get("activityType") or {}).get("typeKey"),
            distance_m=a.get("distance"),
            gain_m=a.get("elevationGain"),
            raw=a,
        ) for a in rows]

    def fetch(self, activity: RemoteActivity) -> RawFile | None:
        data = unwrap(self.api.download_activity(activity.source_id, dl_fmt=self._fmt))
        return RawFile(data, sniff_format(data))
