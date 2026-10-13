"""COROS, direct, through the API the COROS Training Hub website uses.

Unofficial: COROS opens its own API only to approved partners, so this signs in the way
training.coros.com does. COROS can change that at any time, and it signs Talus out whenever you
sign in to Training Hub on the web (one web session per account). Talus offers it to the owner
account only, reads only, and labels it at your own risk.

The password is used once, to sign in, and never stored, not even hashed. What is kept is the
access token, encrypted like the intervals.icu key. When COROS ends that session, Settings asks
for the password again.

intervals.icu is COROS's official partner: anyone can connect COROS there and Talus picks the
runs up through the intervals.icu source, with no COROS sign-in here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import httpx

from .base import RawFile, RemoteActivity, WellnessDay, sniff_format, unwrap

REGIONS = {
    "en": "https://teamapi.coros.com",  # Americas, Asia-Pacific and everywhere else
    "eu": "https://teameuapi.coros.com",
    "cn": "https://teamcnapi.coros.com",
}
PAGE_SIZE = 100  # the API refuses more than 200
WELLNESS_WINDOW_DAYS = 28  # analyse/query is asked a month at a time
TOKEN_INVALID = "1019"
HEADERS = {"Accept": "application/json", "Origin": "https://training.coros.com",
           "Referer": "https://training.coros.com/"}

# COROS sport codes, in the names the other sources use, so scoring, the heatmap and naming
# treat them alike. Codes not listed keep their number ("coros_402").
SPORTS = {
    100: "running", 101: "treadmill_running", 102: "trail_running", 103: "track_running",
    104: "hiking", 105: "mountaineering", 106: "climbing", 900: "walking", 902: "stair_climbing",
    200: "cycling", 201: "indoor_cycling", 202: "cycling", 203: "gravel_cycling", 204: "mountain_biking",
    205: "mountain_biking", 299: "cycling", 300: "lap_swimming", 301: "open_water_swimming",
    400: "cardio", 401: "cardio", 402: "strength_training", 500: "resort_skiing", 501: "snowboarding",
    502: "cross_country_skiing", 503: "backcountry_skiing", 10002: "backcountry_skiing",
    700: "rowing", 701: "indoor_rowing", 10000: "triathlon", 10001: "multi_sport",
}


class CorosError(RuntimeError):
    pass


class CorosSignedOut(CorosError):
    """COROS ended the session Talus held; the owner signs in again."""


def _check(resp: httpx.Response) -> dict:
    if resp.status_code == 429:
        raise CorosError("COROS is rate-limiting requests. Wait a few minutes and try again.")
    try:
        body = resp.json()
    except ValueError:
        raise CorosError(f"COROS answered HTTP {resp.status_code} with something other than JSON.")
    if not isinstance(body, dict) or "result" not in body:
        raise CorosError(f"COROS answered HTTP {resp.status_code} in an unexpected shape.")
    if body["result"] != "0000":
        if body["result"] == TOKEN_INVALID:
            raise CorosSignedOut("COROS signed Talus out, which it does when you sign in to Training Hub "
                                 "on the web. Sign in to COROS again under Data sources.")
        raise CorosError(f"COROS: {body.get('message') or 'request failed'} (code {body['result']})")
    return body.get("data") or {}


def login(email: str, password: str, region: str = "en", client: httpx.Client | None = None) -> dict:
    """Sign in. Returns the credential to save: the token, the COROS user id and the region."""
    if region not in REGIONS:
        raise CorosError(f"Unknown COROS region {region}")
    client = client or httpx.Client(timeout=30, headers=HEADERS)
    body = {"account": email, "accountType": 2, "pwd": hashlib.md5(password.encode()).hexdigest()}
    try:
        data = _check(client.post(f"{REGIONS[region]}/account/login", json=body))
    except httpx.HTTPError as e:
        raise CorosError(f"Couldn't reach COROS: {type(e).__name__}") from e
    if not data.get("accessToken"):
        raise CorosError("COROS accepted the sign-in but sent no token.")
    return {"tokens": data["accessToken"], "user_id": str(data.get("userId") or ""), "region": region}


def local_start(item: dict) -> datetime:
    """Wall-clock start where the activity happened: COROS gives epoch seconds plus the zone in
    quarter-hours east of UTC (8 is UTC+2)."""
    offset = timedelta(minutes=15 * int(item.get("startTimezone") or 0))
    return (datetime.fromtimestamp(int(item["startTime"]), timezone.utc) + offset).replace(tzinfo=None)


class CorosSource:
    name = "coros"

    def __init__(self, token: str, user_id: str = "", region: str = "en", client: httpx.Client | None = None,
                 files: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=60, headers=HEADERS)
        # Files come from a signed storage link on another host: no COROS token goes there.
        self.files = files or httpx.Client(timeout=120, follow_redirects=True)
        self.base = REGIONS.get(region, REGIONS["en"])
        self.client.headers["accesstoken"] = token
        if user_id:
            self.client.headers["yfheader"] = json.dumps({"userId": user_id}, separators=(",", ":"))

    @classmethod
    def from_credential(cls, cred: dict, client: httpx.Client | None = None,
                        files: httpx.Client | None = None) -> "CorosSource":
        """A saved credential: {"kind": "tokens", "tokens": <access token>, "user_id", "region"}."""
        return cls(cred["tokens"], cred.get("user_id") or "", cred.get("region") or "en", client=client, files=files)

    def _get(self, path: str, **params) -> dict:
        return _check(self.client.get(f"{self.base}/{path}", params=params))

    def list_activities(self, since: date, until: date | None = None) -> list[RemoteActivity]:
        out, page = [], 1
        until = until or date.today()
        while True:
            data = self._get("activity/query", size=PAGE_SIZE, pageNumber=page,
                             startDay=since.strftime("%Y%m%d"), endDay=until.strftime("%Y%m%d"))
            batch = data.get("dataList") or []
            for a in batch:
                if not a.get("startTime"):
                    continue
                sport = int(a.get("sportType") or 0)
                out.append(RemoteActivity(
                    source=self.name,
                    source_id=str(a["labelId"]),
                    start_time=local_start(a),
                    name=a.get("name") or None,
                    sport=SPORTS.get(sport, f"coros_{sport}"),
                    distance_m=a.get("distance") or None,
                    gain_m=a.get("ascent") or None,
                    device_name=a.get("device") or None,
                    raw=a,
                ))
            if not batch or page >= int(data.get("totalPage") or 1):
                return out
            page += 1

    def fetch(self, activity: RemoteActivity) -> RawFile | None:
        """The activity's FIT file, which COROS serves from a signed storage link."""
        sport = activity.raw.get("sportType") or 100
        resp = self.client.post(f"{self.base}/activity/detail/download",
                                params={"labelId": activity.source_id, "sportType": sport, "fileType": 4})
        url = _check(resp).get("fileUrl")
        if not url:
            return None
        file = self.files.get(url)
        if file.status_code != 200:
            return None
        data = unwrap(file.content)
        try:
            return RawFile(data, sniff_format(data))
        except ValueError:
            return None

    def list_wellness(self, since: date, until: date | None = None) -> list[WellnessDay]:
        """Overnight HRV and resting HR from COROS's daily EvoLab record. COROS keeps no sleep
        duration there, so sleep stays with another source."""
        out, start, until = [], since, until or date.today()
        while start <= until:
            end = min(until, start + timedelta(days=WELLNESS_WINDOW_DAYS - 1))
            data = self._get("analyse/query", startDay=start.strftime("%Y%m%d"), endDay=end.strftime("%Y%m%d"))
            for d in data.get("dayList") or []:
                hrv, rhr = d.get("avgSleepHrv") or None, d.get("rhr") or None
                if hrv is None and rhr is None:
                    continue
                day = datetime.strptime(str(d["happenDay"]), "%Y%m%d").date()
                out.append(WellnessDay(source=self.name, day=day, hrv=hrv, resting_hr=rhr))
            start = end + timedelta(days=1)
        return out
