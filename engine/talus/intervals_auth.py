"""Connect intervals.icu with OAuth, so people approve Talus instead of pasting an API key.

Needs INTERVALS_CLIENT_ID and INTERVALS_CLIENT_SECRET from an app registered at
intervals.icu/oauth/apply (managed at intervals.icu/settings/apps), with the redirect URI
TALUS_PUBLIC_URL + /api/auth/intervals/callback (by default
http://localhost:3000/api/auth/intervals/callback). Without them Settings shows only the API key form.

intervals.icu issues access tokens only, no refresh tokens: a token lasts until the person
disconnects Talus or connects again. Docs: forum.intervals.icu/t/intervals-icu-oauth-support/2759.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://intervals.icu/oauth/authorize"
TOKEN_URL = "https://intervals.icu/api/oauth/token"
DISCONNECT_URL = "https://intervals.icu/api/v1/disconnect-app"
CALLBACK_PATH = "/api/auth/intervals/callback"
# Read only: activities (list, files, streams) and wellness (HRV, resting HR, sleep).
SCOPE = "ACTIVITY:READ,WELLNESS:READ"


class IntervalsAuthError(ValueError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("INTERVALS_CLIENT_ID") and os.environ.get("INTERVALS_CLIENT_SECRET"))


def redirect_uri() -> str:
    """The web app's address, which proxies /api to the engine."""
    return os.environ.get("TALUS_PUBLIC_URL", "http://localhost:3000").rstrip("/") + CALLBACK_PATH


def authorize_url(state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": os.environ["INTERVALS_CLIENT_ID"], "redirect_uri": redirect_uri(),
        "scope": SCOPE, "state": state})


def exchange_code(code: str) -> dict:
    """Trade the code (good for 2 minutes) for a token. Returns the credential values to save:
    access_token, athlete_id, athlete_name, scope. Swapped out in tests."""
    resp = httpx.post(TOKEN_URL, timeout=20, data={
        "client_id": os.environ["INTERVALS_CLIENT_ID"],
        "client_secret": os.environ["INTERVALS_CLIENT_SECRET"], "code": code})
    if resp.status_code != 200:
        raise IntervalsAuthError(f"intervals.icu refused the connection ({resp.status_code}).")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise IntervalsAuthError("intervals.icu sent no access token.")
    athlete = body.get("athlete") or {}
    scopes = set(str(body.get("scope") or SCOPE).split(","))
    missing = {"ACTIVITY", "WELLNESS"} - {s.split(":")[0] for s in scopes}
    if missing:
        raise IntervalsAuthError("Talus needs to read your activities and wellness. Allow both and try again.")
    return {"access_token": token, "athlete_id": str(athlete.get("id") or "0"),
            "athlete_name": athlete.get("name"), "scope": ",".join(sorted(scopes))}


def disconnect(access_token: str) -> None:
    """Tell intervals.icu to forget Talus for this athlete. Best effort: the token is dropped
    from Talus either way."""
    try:
        httpx.delete(DISCONNECT_URL, timeout=20, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError:
        pass
