"""intervals.icu source, single-athlete mode (personal API key over basic auth).

Multi-user Talus would need intervals.icu OAuth instead. intervals.icu said on 2026-10-09 that
Garmin data through its API will likely be blocked soon, so keep the Garmin adapter as a fallback.
Strava-sourced activities are skipped: the API does not serve them, and Talus does not use Strava data.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import httpx

from .base import RawFile, RemoteActivity, sniff_format, unwrap

BASE_URL = "https://intervals.icu/api/v1"
STREAM_TYPES = "time,distance,altitude,heartrate,cadence,latlng"


class IntervalsSource:
    name = "intervals"

    def __init__(self, api_key: str | None = None, athlete_id: str | None = None,
                 client: httpx.Client | None = None):
        api_key = api_key or os.environ.get("INTERVALS_API_KEY")
        if not api_key:
            raise RuntimeError("Set INTERVALS_API_KEY (intervals.icu > Settings > Developer Settings)")
        # "0" means the athlete that owns the API key.
        self.athlete_id = athlete_id or os.environ.get("INTERVALS_ATHLETE_ID") or "0"
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=60)
        self.client.auth = httpx.BasicAuth("API_KEY", api_key)

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]:
        params = {"oldest": since.isoformat(), "newest": (until or date.today()).isoformat()}
        resp = self.client.get(f"/athlete/{self.athlete_id}/activities", params=params)
        resp.raise_for_status()
        out = []
        for a in resp.json():
            if a.get("source") == "STRAVA":
                continue
            out.append(RemoteActivity(
                source=self.name,
                source_id=str(a["id"]),
                start_time=datetime.fromisoformat(a["start_date_local"]),
                name=a.get("name"),
                sport=a.get("type"),
                distance_m=a.get("distance"),
                gain_m=a.get("total_elevation_gain"),
                device_name=a.get("device_name"),
                raw=a,
            ))
        return out

    def fetch(self, activity: RemoteActivity) -> RawFile | None:
        """Prefer the original uploaded file; fall back to the streams JSON."""
        resp = self.client.get(f"/activity/{activity.source_id}/file")
        if resp.status_code == 200 and resp.content:
            data = unwrap(resp.content)
            try:
                return RawFile(data, sniff_format(data))
            except ValueError:
                pass
        resp = self.client.get(f"/activity/{activity.source_id}/streams.json",
                               params={"types": STREAM_TYPES})
        if resp.status_code != 200:
            return None
        streams = resp.json()
        if not streams:
            return None
        return RawFile(json.dumps(streams).encode(), "icu-streams")
