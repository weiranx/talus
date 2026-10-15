"""Local ChatGPT plan provider. Tokens stay encrypted in accounts.db, never in the UI."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

from .accounts import Accounts

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_lock = threading.RLock()
_pending: dict[tuple[str, int], dict] = {}
_jwks = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json")


def enabled() -> bool:
    # Deliberate opt-in: a hosted engine cannot receive the user's loopback callback.
    return os.environ.get("TALUS_CHATGPT_LOCAL") == "1"


def status(accounts: Accounts, account_id: int) -> dict:
    cred = accounts.credential(account_id, "chatgpt") or {}
    pending = _pending.get((str(accounts.root.resolve()), account_id), {})
    return {"available": enabled(), "connected": bool(cred.get("access_token")),
            "sharing": "chatgpt.tokens.use.direct" in cred.get("scopes", []),
            "email": cred.get("email"), "pending": pending.get("state") == "pending",
            "error": pending.get("error")}


def _token(data: dict) -> dict:
    try:
        response = httpx.post(ISSUER + "/api/accounts/oauth/token", data=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        if not result.get("access_token") or result.get("token_type", "").lower() != "bearer":
            raise ValueError("OpenAI did not return a bearer credential.")
        return result
    except httpx.HTTPError as exc:
        raise ValueError("ChatGPT authorization expired or failed. Reconnect in Settings.") from exc


def _save(accounts: Accounts, account_id: int, old: dict, tokens: dict) -> dict:
    values = {**old, **tokens, "expires_at": time.time() + float(tokens.get("expires_in", 3600)),
              "scopes": tokens["scope"].split() if "scope" in tokens else old.get("scopes", [])}
    accounts.set_credential(account_id, "chatgpt", "oauth", values)
    return values


def start(accounts: Accounts, account_id: int) -> str:
    if not enabled():
        raise ValueError("ChatGPT sign-in requires a local engine with TALUS_CHATGPT_LOCAL=1.")
    root = accounts.root.resolve()
    key = (str(root), account_id)
    with _lock:
        if _pending.get(key, {}).get("state") == "pending":
            raise ValueError("A ChatGPT sign-in is already open. Finish it or wait for it to expire.")
        host_id = accounts.config("chatgpt_host_id")
        if not host_id:
            host_id = "urn:uuid:" + str(uuid.uuid4())
            accounts.set_config("chatgpt_host_id", host_id)
        old = accounts.credential(account_id, "chatgpt") or {}
        client_id = old.get("client_id", "dynamic_agent_client")
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        attempt = {"state": "pending"}
        _pending[key] = attempt

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # The callback URL contains an authorization code.

        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path != "/auth/callback" or not secrets.compare_digest(query.get("state", [""])[0], state):
                self.send_error(400, "Invalid sign-in callback")
                return
            try:
                if time.monotonic() >= deadline:
                    raise ValueError("ChatGPT sign-in expired. Try again.")
                if query.get("error"):
                    raise ValueError("ChatGPT sign-in was declined. Try again to enable plan usage.")
                issued = query.get("client_id", [client_id])[0]
                if issued == "dynamic_agent_client" or (client_id != "dynamic_agent_client" and issued != client_id):
                    raise ValueError("ChatGPT returned an unexpected registration.")
                code = query.get("code", [""])[0]
                if not code:
                    raise ValueError("ChatGPT returned no authorization code.")
                # Retain the registration even if code redemption fails.
                db = Accounts(root)
                registration = {**old, "client_id": issued}
                with _lock:
                    if _pending.get(key) is not attempt:
                        raise ValueError("This sign-in was canceled.")
                    db.set_credential(account_id, "chatgpt", "oauth", registration)
                tokens = _token({"grant_type": "authorization_code", "client_id": issued,
                                 "code": code, "code_verifier": verifier, "redirect_uri": redirect,
                                 "resource": RESOURCE})
                identity = jwt.decode(tokens.get("id_token", ""),
                                      _jwks.get_signing_key_from_jwt(tokens.get("id_token", "")).key,
                                      algorithms=["RS256"], audience=issued, issuer=ISSUER,
                                      options={"require": ["sub", "exp", "iat", "nonce"]}, leeway=5)
                if identity["nonce"] != nonce or not identity["sub"] or (old.get("subject") and identity["sub"] != old["subject"]):
                    raise ValueError("ChatGPT returned an unexpected identity.")
                with _lock:
                    if _pending.get(key) is not attempt:
                        raise ValueError("This sign-in was canceled.")
                    _save(db, account_id, {**registration, "scopes": [], "subject": identity["sub"], "email": identity.get("email")}, tokens)
                    attempt.update(state="complete")
                message = "ChatGPT connected. Return to Talus Settings and choose your model."
            except (ValueError, jwt.PyJWTError, httpx.HTTPError):
                attempt.update(state="failed", error="ChatGPT sign-in failed or was declined. Please try again.")
                message = attempt["error"]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(message.encode())

    try:
        server = HTTPServer(("127.0.0.1", 0), Callback)
    except OSError as exc:
        attempt.update(state="failed", error="Could not open the local sign-in listener.")
        raise ValueError(attempt["error"]) from exc
    redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
    deadline = time.monotonic() + 300
    server.timeout = 1

    def listen():
        try:
            while attempt["state"] == "pending" and time.monotonic() < deadline and _pending.get(key) is attempt:
                server.handle_request()
            if attempt["state"] == "pending":
                attempt.update(state="failed", error="ChatGPT sign-in expired. Try again.")
        finally:
            server.server_close()

    threading.Thread(target=listen, daemon=True).start()
    params = {"client_id": client_id, "ext_agent_host_id": host_id, "redirect_uri": redirect,
              "response_type": "code", "scope": SCOPE, "resource": RESOURCE, "state": state,
              "nonce": nonce, "code_challenge_method": "S256",
              "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()}
    if client_id == "dynamic_agent_client":
        params["agent_name_hint"] = "Talus"
    elif old.get("email"):
        params["login_hint"] = old["email"]
    return ISSUER + "/api/accounts/authorize?" + urlencode(params)


def disconnect(accounts: Accounts, account_id: int):
    with _lock:
        _pending.pop((str(accounts.root.resolve()), account_id), None)
        accounts.clear_credential(account_id, "chatgpt")


class Client:
    def __init__(self, accounts: Accounts, account_id: int):
        self.accounts, self.account_id = accounts, account_id

    def access_token(self) -> str:
        with _lock:
            cred = self.accounts.credential(self.account_id, "chatgpt") or {}
            if not enabled() or "chatgpt.tokens.use.direct" not in cred.get("scopes", []):
                raise ValueError("Connect ChatGPT and enable ChatGPT plan usage in Settings.")
            if cred.get("expires_at", 0) <= time.time() + 60:
                if not cred.get("refresh_token"):
                    raise ValueError("Reconnect ChatGPT in Settings.")
                tokens = _token({"grant_type": "refresh_token", "client_id": cred["client_id"],
                                 "refresh_token": cred["refresh_token"], "resource": RESOURCE})
                cred = _save(self.accounts, self.account_id, cred, tokens)
            if "chatgpt.tokens.use.direct" not in cred.get("scopes", []):
                raise ValueError("Enable ChatGPT plan usage by reconnecting in Settings.")
            return cred["access_token"]

    def models(self) -> list[dict]:
        try:
            r = httpx.get(RESOURCE + "/models", headers={"Authorization": "Bearer " + self.access_token()}, timeout=30)
            r.raise_for_status()
            return [{"id": m["slug"], "label": m.get("display_name", m["slug"])}
                    for m in r.json().get("models", []) if m.get("visibility") == "list" and m.get("slug")]
        except httpx.HTTPError as exc:
            raise ValueError("Could not load ChatGPT models. Reconnect or check Manage usage.") from exc

    def ask(self, model: str, instructions: str, text: str, schema: dict, max_tokens: int):
        if model == "unselected":
            raise ValueError("Choose a ChatGPT model in Settings.")
        payload = {"model": model, "instructions": instructions, "input": text, "store": False, "stream": True,
                   "max_output_tokens": max_tokens,
                   "text": {"format": {"type": "json_schema", "name": "coach_reply", "schema": schema, "strict": False}}}
        try:
            with httpx.stream("POST", RESOURCE + "/responses", headers={"Authorization": "Bearer " + self.access_token()},
                              json=payload, timeout=180) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    event = json.loads(line[5:])
                    kind = event.get("type")
                    if kind in ("response.failed", "response.incomplete", "error"):
                        raise ValueError("ChatGPT request failed. Check Manage usage or reconnect in Settings.")
                    if kind == "response.completed":
                        response = event["response"]
                        body = "".join(c.get("text", "") for item in response.get("output", [])
                                       for c in item.get("content", []) if c.get("type") == "output_text")
                        reply = json.loads(body)
                        if not isinstance(reply, dict):
                            raise ValueError("ChatGPT returned an invalid coach reply.")
                        usage = SimpleNamespace(**(response.get("usage") or {}))
                        resp = SimpleNamespace(model="chatgpt/" + model, usage=usage, stop_reason="end_turn")
                        return resp, reply
            raise ValueError("ChatGPT stream ended before completion.")
        except httpx.HTTPError as exc:
            raise ValueError("ChatGPT request failed. Check Manage usage or reconnect in Settings.") from exc
