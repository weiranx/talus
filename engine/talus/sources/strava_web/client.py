"""A signed-in session on strava.com, borrowed from the owner's own browser. Strava's public API
is deliberately not used.

Personal only: this exists so the owner's own runs still reach Talus when Garmin rate-limits
intervals.icu. It runs only for the owner account with `strava_web_enabled` set in accounts.db,
and it is never offered as a source in the apps. Scraping the website is against Strava's terms;
keep it to your own account.

Talus never signs in with a password. Strava's sign-in page is an email-first app now (no
authenticity_token, no form posting to /session), and posting a password from a server is what
gets an account flagged. Instead the Strava card in Settings > Data sources (or `talus strava-web
setup`) takes the Cookie header from a browser that is signed in, checks it, and saves it
encrypted like the Garmin tokens. STRAVA_SESSION_COOKIE in .env overrides it.

The session's cookies, as Strava refreshes them, are kept in the account's folder
(strava_web_cookies.json, mode 0600, outside raw/ so an export leaves them out) and reused across
runs. When Strava sends a request to /login or answers 401 or 403, the session has ended: the run
stops with `session_expired` until setup is run again. A captcha stops it the same way. Neither is
retried. One request at a time, at most one a second, with a desktop browser's User-Agent.
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
SETUP = "Run `talus strava-web setup` with a fresh cookie from a browser signed in to Strava."

CSRF_META = re.compile(r'<meta[^>]*name="csrf-token"[^>]*content="([^"]*)"|'
                       r'<meta[^>]*content="([^"]*)"[^>]*name="csrf-token"', re.I)
# A challenge, not just the script: signed-in pages may load reCAPTCHA invisibly on every visit.
CAPTCHA = re.compile(r'class="[^"]*\b(g-recaptcha|h-captcha|cf-turnstile)\b|/captcha|captcha-challenge', re.I)


class StravaWebError(RuntimeError):
    """A Strava web sync that stopped. `code` says why, for the sync record."""
    code = "error"


class StravaSessionExpired(StravaWebError):
    code = "session_expired"


class StravaCaptchaError(StravaWebError):
    code = "captcha"


def cookie_path(account_dir: str | Path) -> Path:
    return Path(account_dir) / COOKIE_FILE


def clean_cookie_header(text: str) -> str:
    """What someone pastes: the header's value, maybe with "Cookie:" in front, maybe wrapped."""
    text = " ".join(text.split())
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    return "; ".join(p.strip() for p in text.split(";") if "=" in p)


class StravaWebClient:
    def __init__(self, cookie_file: str | Path, session_cookie: str | None = None,
                 saved_cookie: str | None = None, transport: httpx.BaseTransport | None = None,
                 clock=time.monotonic, sleep=time.sleep):
        """session_cookie (STRAVA_SESSION_COOKIE) wins; then the jar on disk, which carries the
        cookies Strava refreshed on earlier runs; then saved_cookie, from setup."""
        self.cookie_file = Path(cookie_file)
        self.http = httpx.Client(base_url=BASE_URL, transport=transport, follow_redirects=False, timeout=60,
                                 headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self.csrf: str | None = None
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None
        if session_cookie:
            self._use(session_cookie)
        else:
            self._load()
            if not len(self.http.cookies.jar) and saved_cookie:
                self._use(saved_cookie)

    def _use(self, header: str) -> None:
        for part in clean_cookie_header(header).split("; "):
            name, _, value = part.partition("=")
            if name:
                self.http.cookies.set(name, value, domain=COOKIE_DOMAIN, path="/")

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
        return resp

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """A request in the borrowed session. Redirects are followed by hand, stopping at one to
        the sign-in page; the cookies Strava sets along the way are kept for the next run."""
        if not len(self.http.cookies.jar):
            raise StravaSessionExpired("Talus has no Strava session yet. " + SETUP)
        resp = self._send(method, url, **kwargs)
        for _ in range(MAX_REDIRECTS):
            if not resp.is_redirect or _to_login(resp):
                break
            resp = self._send("GET", str(resp.request.url.join(resp.headers["location"])))
        _no_captcha(resp)
        if _to_login(resp) or resp.status_code in (401, 403):
            raise StravaSessionExpired(f"Strava's session has ended ({resp.status_code}). " + SETUP)
        self._save()
        return resp

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def close(self) -> None:
        self.http.close()


def _to_login(resp: httpx.Response) -> bool:
    location = resp.headers.get("location", "")
    return resp.is_redirect and httpx.URL(location).path.rstrip("/") in ("/login", "/session")


def _no_captcha(resp: httpx.Response) -> None:
    if CAPTCHA.search(resp.headers.get("location", "")) or (
            "html" in resp.headers.get("content-type", "") and CAPTCHA.search(resp.text)):
        raise StravaCaptchaError("Strava wants a captcha solved. Open strava.com in your browser, "
                                 "then run `talus strava-web setup` again.")
