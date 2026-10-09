"""Sign in with Google (OpenID Connect, authorization code flow).

Needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET from a Google Cloud OAuth client of type "Web
application", with the redirect URI TALUS_PUBLIC_URL + /api/auth/google/callback (by default
http://localhost:3000/api/auth/google/callback). Without them the Google button is hidden.

The ID token comes straight from Google's token endpoint over TLS, which OpenID Connect allows in
place of checking its signature (Core 1.0, section 3.1.3.7); its issuer, audience, expiry, nonce
and email_verified are still checked.
"""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
CALLBACK_PATH = "/api/auth/google/callback"


class GoogleError(ValueError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def redirect_uri() -> str:
    """Where Google sends people back: the web app's address, which proxies /api to the engine."""
    return os.environ.get("TALUS_PUBLIC_URL", "http://localhost:3000").rstrip("/") + CALLBACK_PATH


def authorize_url(state: str, nonce: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"], "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": "openid email", "state": state, "nonce": nonce, "prompt": "select_account"})


def exchange_code(code: str) -> dict:
    """Trade the code for an ID token and return its claims. Swapped out in tests."""
    resp = httpx.post(TOKEN_URL, timeout=20, data={
        "code": code, "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"], "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code"})
    if resp.status_code != 200:
        raise GoogleError(f"Google refused the sign-in ({resp.status_code}).")
    token = resp.json().get("id_token")
    if not token:
        raise GoogleError("Google sent no ID token.")
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise GoogleError("Google sent an unreadable ID token.") from e


def check_claims(claims: dict, nonce: str) -> tuple[str, str]:
    """The Google subject id and verified email, or GoogleError."""
    if claims.get("iss") not in ISSUERS:
        raise GoogleError("The sign-in didn't come from Google.")
    aud = claims.get("aud")
    if os.environ["GOOGLE_CLIENT_ID"] not in (aud if isinstance(aud, list) else [aud]):
        raise GoogleError("The sign-in was meant for another app.")
    if float(claims.get("exp", 0)) < time.time():
        raise GoogleError("The sign-in expired. Try again.")
    if claims.get("nonce") != nonce:
        raise GoogleError("The sign-in didn't match this browser. Try again.")
    if not claims.get("sub") or not claims.get("email"):
        raise GoogleError("Google didn't share an email address.")
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleError("Verify your email with Google first.")
    return str(claims["sub"]), str(claims["email"])
