"""A signed-in session on strava.com, the way the website itself signs in. Strava's public API is
deliberately not used.

Personal only: this exists so the owner's own runs still reach Talus when Garmin rate-limits
intervals.icu. It runs only for the owner account with `strava_web_enabled` set in accounts.db,
and it is never offered as a source in the apps. Scraping the website is against Strava's terms;
keep it to your own account.

STRAVA_EMAIL and STRAVA_PASSWORD come from .env, like GARMIN_EMAIL and GARMIN_PASSWORD. The
password is only ever sent to strava.com/session. The session's cookies are kept in the account's
folder (strava_web_cookies.json, mode 0600, outside raw/ so an export leaves them out) and reused
across runs; Talus signs in again only when Strava sends it to /login or answers 401 or 403.
STRAVA_SESSION_COOKIE, a whole Cookie header copied from a signed-in browser, overrides the jar.

A captcha or a refused sign-in stops the run with an error and is never retried. One request at a
time, at most one a second, with a desktop browser's User-Agent.
"""

from __future__ import annotations

import json
import os
import re
import time
from http.cookiejar import Cookie
from pathlib import Path

import httpx

BASE_URL = "https://www.strava.com"
COOKIE_DOMAIN = ".strava.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
MIN_INTERVAL_S = 1.0
MAX_REDIRECTS = 5
COOKIE_FILE = "strava_web_cookies.json"

TOKEN_INPUT = re.compile(r'<input[^>]*name="authenticity_token"[^>]*>', re.I)
VALUE = re.compile(r'value="([^"]*)"')
CSRF_META = re.compile(r'<meta[^>]*name="csrf-token"[^>]*content="([^"]*)"|'
                       r'<meta[^>]*content="([^"]*)"[^>]*name="csrf-token"', re.I)
# A challenge, not just the script: Strava's pages may load reCAPTCHA invisibly on every visit.
# Unverified against a recording (there is no HAR yet); widen if a live run meets one unnoticed.
CAPTCHA = re.compile(r'class="[^"]*\b(g-recaptcha|h-captcha|cf-turnstile)\b|/captcha|captcha-challenge', re.I)


class StravaWebError(RuntimeError):
    """A Strava web sync that stopped. `code` says why, for the sync record."""
    code = "error"


class StravaLoginError(StravaWebError):
    code = "login_failed"


class StravaCaptchaError(StravaWebError):
    code = "captcha"


def cookie_path(account_dir: str | Path) -> Path:
    return Path(account_dir) / COOKIE_FILE


class StravaWebClient:
    def __init__(self, cookie_file: str | Path, email: str | None = None, password: str | None = None,
                 session_cookie: str | None = None, transport: httpx.BaseTransport | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        self.cookie_file = Path(cookie_file)
        self.email, self.password = email, password
        self.http = httpx.Client(base_url=BASE_URL, transport=transport, follow_redirects=False, timeout=60,
                                 headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self.csrf: str | None = None
        self.logins = 0
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None
        if session_cookie:
            for part in session_cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name:
                    self.http.cookies.set(name, value, domain=COOKIE_DOMAIN, path="/")
        else:
            self._load()

    @classmethod
    def from_env(cls, cookie_file: str | Path, **kwargs) -> "StravaWebClient":
        return cls(cookie_file, email=os.environ.get("STRAVA_EMAIL"), password=os.environ.get("STRAVA_PASSWORD"),
                   session_cookie=os.environ.get("STRAVA_SESSION_COOKIE"), **kwargs)

    # Cookie jar on disk
    def _load(self) -> None:
        if not self.cookie_file.is_file():
            return
        if self.cookie_file.stat().st_mode & 0o077:
            os.chmod(self.cookie_file, 0o600)
        try:
            saved = json.loads(self.cookie_file.read_text())
        except (OSError, ValueError):
            return
        self.csrf = saved.get("csrf")
        now = time.time()
        for c in saved.get("cookies", []):
            if c.get("expires") is not None and c["expires"] < now:
                continue
            self.http.cookies.jar.set_cookie(Cookie(
                0, c["name"], c["value"], None, False, c["domain"], True, c["domain"].startswith("."),
                c["path"], True, c["secure"], c.get("expires"), c.get("expires") is None, None, None, {}))

    def _save(self) -> None:
        cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path, "secure": c.secure,
                    "expires": c.expires} for c in self.http.cookies.jar]
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cookie_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"csrf": self.csrf, "cookies": cookies}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.cookie_file)

    # Requests
    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One request, paced to MIN_INTERVAL_S, with the CSRF token on anything but a GET."""
        if self._last is not None:
            wait = MIN_INTERVAL_S - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        headers = dict(kwargs.pop("headers", None) or {})
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        try:
            resp = self.http.request(method, url, headers=headers, **kwargs)
        finally:
            self._last = self._clock()
        if "html" in resp.headers.get("content-type", ""):
            m = CSRF_META.search(resp.text)
            if m:
                self.csrf = m.group(1) or m.group(2)
        self._save()
        return resp

    def _follow(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Follow redirects by hand, stopping at one to the sign-in page."""
        resp = self._send(method, url, **kwargs)
        for _ in range(MAX_REDIRECTS):
            if not resp.is_redirect or _to_login(resp):
                return resp
            resp = self._send("GET", str(resp.request.url.join(resp.headers["location"])))
        return resp

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """A request in the signed-in session: signs in first when there is no session, and once
        more when Strava sends this one to sign-in."""
        if not len(self.http.cookies.jar):
            self.login()
        resp = self._follow(method, url, **kwargs)
        if _needs_login(resp):
            self.login()
            resp = self._follow(method, url, **kwargs)
            if _needs_login(resp):
                raise StravaLoginError("Strava still asks Talus to sign in right after signing in.")
        _no_captcha(resp)
        return resp

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def login(self) -> None:
        """GET /login for its authenticity_token, then POST /session. A captcha or a refusal
        raises; neither is retried."""
        if not (self.email and self.password):
            raise StravaLoginError("Strava's session has ended and STRAVA_EMAIL and STRAVA_PASSWORD aren't set in .env.")
        if self.logins:
            raise StravaLoginError("Strava asked Talus to sign in twice in one run; stopping.")
        self.logins += 1
        self.http.cookies.clear()
        page = self._send("GET", "/login")
        _no_captcha(page)
        field = TOKEN_INPUT.search(page.text)
        token = field and VALUE.search(field.group(0))
        if not token:
            raise StravaLoginError(f"Strava's sign-in page had no authenticity_token ({page.status_code}).")
        self.csrf = self.csrf or token.group(1)
        resp = self._send("POST", "/session", data={
            "utf8": "✓", "authenticity_token": token.group(1), "plan": "",
            "email": self.email, "password": self.password, "remember_me": "on"})
        _no_captcha(resp)
        if not resp.is_redirect or _to_login(resp):
            raise StravaLoginError(f"Strava didn't accept the email and password ({resp.status_code}).")
        self._save()

    def close(self) -> None:
        self.http.close()


def _to_login(resp: httpx.Response) -> bool:
    location = resp.headers.get("location", "")
    return resp.is_redirect and httpx.URL(location).path.rstrip("/") in ("/login", "/session")


def _needs_login(resp: httpx.Response) -> bool:
    return resp.status_code in (401, 403) or _to_login(resp)


def _no_captcha(resp: httpx.Response) -> None:
    if CAPTCHA.search(resp.headers.get("location", "")) or (
            "html" in resp.headers.get("content-type", "") and CAPTCHA.search(resp.text)):
        raise StravaCaptchaError("Strava wants a captcha solved. Sign in once at strava.com from a browser, "
                                 "or set STRAVA_SESSION_COOKIE, then sync again.")
