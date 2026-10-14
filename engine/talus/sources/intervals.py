"""intervals.icu source. Each Talus account syncs with its own credential.

Either an OAuth access token (Settings > Connect intervals.icu, see intervals_auth.py; Bearer
auth; 100 requests per user per day, 500 users by default) or a personal API key over basic auth,
which still works as a fallback. intervals.icu said on 2026-10-09 that
Garmin data through its API will likely be blocked soon, so keep the Garmin adapter as a fallback.
Strava-sourced activities are skipped: the API does not serve them, and no account gets Strava data
from intervals.icu. (The owner's personal Strava fallback is sources/strava_web, never offered to others.)
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

import httpx

from .base import RawFile, RemoteActivity, WellnessDay, sniff_format, unwrap

BASE_URL = "https://intervals.icu/api/v1"
STREAM_TYPES = "time,distance,altitude,heartrate,cadence,latlng"


class IntervalsSource:
    name = "intervals"

    def __init__(self, api_key: str | None = None, athlete_id: str | None = None,
                 client: httpx.Client | None = None, access_token: str | None = None):
        api_key = api_key or (None if access_token else os.environ.get("INTERVALS_API_KEY"))
        if not api_key and not access_token:
            raise RuntimeError("Set INTERVALS_API_KEY (intervals.icu > Settings > Developer Settings)")
        # "0" means the athlete that owns the API key or token.
        self.athlete_id = athlete_id or (None if access_token else os.environ.get("INTERVALS_ATHLETE_ID")) or "0"
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=60)
        self.oauth = bool(access_token)
        if access_token:
            self.client.headers["Authorization"] = f"Bearer {access_token}"
        else:
            self.client.auth = httpx.BasicAuth("API_KEY", api_key)

    @classmethod
    def from_credential(cls, cred: dict, client: httpx.Client | None = None) -> "IntervalsSource":
        """A saved credential: {"kind": "api_key", "api_key", "athlete_id"} or
        {"kind": "oauth", "access_token", "athlete_id"}."""
        if cred.get("kind") == "oauth":
            return cls(athlete_id=cred.get("athlete_id"), client=client, access_token=cred["access_token"])
        # Never fall back to the server's INTERVALS_API_KEY here: that key is the owner's.
        api_key = (cred.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("The saved intervals.icu key is blank. Add it again in Settings > Data source.")
        return cls(api_key=api_key, athlete_id=cred.get("athlete_id") or "0", client=client)

    def _ok(self, resp: httpx.Response) -> None:
        if self.oauth and resp.status_code in (401, 403):
            raise RuntimeError("intervals.icu no longer accepts Talus's connection. Connect again in Settings > Data sources.")
        resp.raise_for_status()

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]:
        params = {"oldest": since.isoformat(), "newest": (until or date.today()).isoformat()}
        resp = self.client.get(f"/athlete/{self.athlete_id}/activities", params=params)
        self._ok(resp)
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

    def list_wellness(self, since: date, until: date | None = None) -> list[WellnessDay]:
        """Daily wellness: overnight HRV (rMSSD), resting HR and sleep, one record per day."""
        params = {"oldest": since.isoformat(), "newest": (until or date.today()).isoformat()}
        resp = self.client.get(f"/athlete/{self.athlete_id}/wellness", params=params)
        self._ok(resp)
        out = []
        for w in resp.json():
            day = WellnessDay(source=self.name, day=date.fromisoformat(w["id"][:10]), hrv=w.get("hrv") or None,
                              resting_hr=w.get("restingHR") or None, sleep_s=w.get("sleepSecs") or None,
                              sleep_score=w.get("sleepScore") or None)
            if any(v is not None for v in (day.hrv, day.resting_hr, day.sleep_s)):
                out.append(day)
        return out
