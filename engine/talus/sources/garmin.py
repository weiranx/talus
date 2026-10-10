"""Garmin Connect, direct, through the unofficial python-garminconnect library.

Unofficial: Garmin's own API is closed to new apps, so this signs in the way the Garmin Connect
website does. Garmin can change that at any time, or rate-limit or lock an account that syncs
too often. Talus offers it to the owner account only and labels it at your own risk.

The password is used once, to sign in, and never stored. What is kept is the library's token
bundle (about six months of access), encrypted like the intervals.icu key. Install with:
uv sync --extra garmin
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta

from .base import RawFile, RemoteActivity, WellnessDay, sniff_format, unwrap

# A sign-in waiting on its MFA code lives in memory, per account, for this long.
MFA_TTL_S = 300
_pending: dict[int, tuple[float, object]] = {}
_pending_lock = threading.Lock()


class GarminLoginError(RuntimeError):
    pass


def _garmin():
    try:
        from garminconnect import Garmin
    except ImportError as e:  # the optional extra
        raise GarminLoginError("Garmin support isn't installed on the engine: run uv sync --extra garmin") from e
    return Garmin


def _login_error(e: Exception) -> GarminLoginError:
    name = type(e).__name__
    if "TooManyRequests" in name or "429" in str(e):
        return GarminLoginError("Garmin is rate-limiting sign-ins. Wait a few minutes and try again.")
    if "Authentication" in name:
        return GarminLoginError("Garmin didn't accept that email and password.")
    return GarminLoginError(f"Garmin sign-in failed: {name}: {e}")


def start_login(account_id: int, email: str, password: str) -> str | None:
    """Sign in. Returns the token bundle to save, or None when Garmin wants an MFA code, which
    finish_login() then takes."""
    Garmin = _garmin()
    api = Garmin(email, password, return_on_mfa=True)
    try:
        status, _ = api.login()
    except Exception as e:
        raise _login_error(e) from e
    if status == "needs_mfa":
        with _pending_lock:
            _pending[account_id] = (time.monotonic(), api)
        return None
    return api.client.dumps()


def finish_login(account_id: int, code: str) -> str:
    with _pending_lock:
        entry = _pending.pop(account_id, None)
    if entry is None or time.monotonic() - entry[0] > MFA_TTL_S:
        raise GarminLoginError("That sign-in expired. Enter your Garmin email and password again.")
    api = entry[1]
    try:
        api.resume_login(None, code.strip())
    except Exception as e:
        raise _login_error(e) from e
    return api.client.dumps()


def mfa_pending(account_id: int) -> bool:
    with _pending_lock:
        entry = _pending.get(account_id)
    return entry is not None and time.monotonic() - entry[0] <= MFA_TTL_S


class GarminSource:
    name = "garmin"
    # Overnight data comes one request per day, so a sync asks only for days it lacks.
    wellness_per_day = True

    def __init__(self, tokens: str | None = None, api=None, email: str | None = None, password: str | None = None):
        if api is None:
            Garmin = _garmin()
            api = Garmin(email, password)
            try:
                api.login(tokens)
            except Exception as e:
                raise _login_error(e) from e
        self.api = api
        self._tokens = tokens

    @classmethod
    def from_credential(cls, cred: dict) -> "GarminSource":
        """A saved credential: {"kind": "tokens", "tokens": <the library's token JSON>}."""
        return cls(tokens=cred["tokens"])

    def tokens(self) -> str | None:
        """The current token bundle; it changes when the library refreshes it during a sync."""
        try:
            return self.api.client.dumps()
        except Exception:
            return self._tokens

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
            device_name=None,
            raw=a,
        ) for a in rows]

    def fetch(self, activity: RemoteActivity) -> RawFile | None:
        fmt = self.api.ActivityDownloadFormat.ORIGINAL
        data = unwrap(self.api.download_activity(activity.source_id, dl_fmt=fmt))
        try:
            return RawFile(data, sniff_format(data))
        except ValueError:
            return None

    def list_wellness(self, since: date, until: date | None = None) -> list[WellnessDay]:
        """Overnight HRV (rMSSD), resting HR and sleep from each day's sleep record."""
        out = []
        day, until = since, until or date.today()
        while day <= until:
            try:
                w = self.api.get_sleep_data(day.isoformat()) or {}
            except Exception:
                w = {}
            dto = w.get("dailySleepDTO") or {}
            rec = WellnessDay(source=self.name, day=day, hrv=w.get("avgOvernightHrv") or None,
                              resting_hr=w.get("restingHeartRate") or None,
                              sleep_s=dto.get("sleepTimeSeconds") or None,
                              sleep_score=((dto.get("sleepScores") or {}).get("overall") or {}).get("value"))
            if any(v is not None for v in (rec.hrv, rec.resting_hr, rec.sleep_s)):
                out.append(rec)
            day += timedelta(days=1)
        return out
