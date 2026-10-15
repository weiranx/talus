import io
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from talus import api, chatgpt, coach, settings
from talus.accounts import Accounts
from tests.conftest import sign_in


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TALUS_CHATGPT_LOCAL", "1")
    monkeypatch.setattr(api, "load_env", lambda: None)
    chatgpt._pending.clear()


def test_auth_and_account_isolation(tmp_path):
    anonymous = TestClient(api.app)
    assert anonymous.get("/api/settings/chatgpt").status_code == 401
    client = sign_in()
    accounts = Accounts(tmp_path)
    accounts.set_credential(1, "chatgpt", "oauth", {"email": "one@example.com", "access_token": "secret",
                                                  "scopes": ["chatgpt.tokens.use.direct"]})
    response = client.get("/api/settings/chatgpt")
    assert response.json()["email"] == "one@example.com"
    assert "secret" not in response.text
    friend = sign_in("friend@example.com", "another long password")
    assert not friend.get("/api/settings/chatgpt").json()["connected"]
    assert friend.delete("/api/settings/chatgpt").status_code == 200
    assert client.get("/api/settings/chatgpt").json()["connected"]
    assert client.post("/api/settings/chatgpt/connect").status_code == 422  # testserver isn't loopback


def flow(tmp_path, monkeypatch):
    captured = {}
    class Server:
        server_port = 54321
        timeout = 0
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
            captured["handler"] = handler
    monkeypatch.setattr(chatgpt, "HTTPServer", Server)
    monkeypatch.setattr(chatgpt.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    sign_in()
    accounts = Accounts(tmp_path)
    url = chatgpt.start(accounts, 1)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    handler = captured["handler"].__new__(captured["handler"])
    handler.wfile = io.BytesIO()
    handler.send_error = lambda *args: captured.update(error=args)
    handler.send_response = handler.send_header = handler.end_headers = lambda *args: None
    return accounts, query, handler, captured


def test_pkce_state_and_callback_identity(tmp_path, monkeypatch):
    accounts, query, handler, captured = flow(tmp_path, monkeypatch)
    assert query["client_id"] == "dynamic_agent_client"
    assert query["code_challenge_method"] == "S256"
    assert query["ext_agent_host_id"].startswith("urn:uuid:")
    handler.path = "/auth/callback?state=wrong&code=stolen"
    handler.do_GET()
    assert captured["error"][0] == 400
    assert not accounts.credential(1, "chatgpt")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(chatgpt._jwks, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=key.public_key()))
    identity = jwt.encode({"iss": chatgpt.ISSUER, "aud": "oaiapp_test", "exp": time.time() + 60,
                           "iat": time.time(), "sub": "user", "nonce": query["nonce"], "email": "one@example.com"}, key, algorithm="RS256")
    def tokens(data):
        assert data["client_id"] == "oaiapp_test"
        assert data["redirect_uri"] == query["redirect_uri"]
        assert data["code_verifier"] != query["code_challenge"]
        return {"id_token": identity, "access_token": "private", "scope": "openid chatgpt.tokens.use.direct"}
    monkeypatch.setattr(chatgpt, "_token", tokens)
    handler.path = f'/auth/callback?state={query["state"]}&code=ok&client_id=oaiapp_test'
    handler.do_GET()
    cred = accounts.credential(1, "chatgpt")
    assert cred["subject"] == "user" and cred["client_id"] == "oaiapp_test"
    assert chatgpt.status(accounts, 1)["sharing"]
    assert "private" not in (tmp_path / "accounts.db").read_bytes().decode(errors="ignore")


@pytest.mark.parametrize("failure", ["nonce", "signature", "issuer", "audience", "expiry", "denied", "canceled"])
def test_callback_rejects_invalid_or_canceled(tmp_path, monkeypatch, failure):
    accounts, query, handler, _ = flow(tmp_path, monkeypatch)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(chatgpt._jwks, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=other.public_key() if failure == "signature" else key.public_key()))
    identity = jwt.encode({"iss": "https://wrong.example" if failure == "issuer" else chatgpt.ISSUER,
                           "aud": "wrong-client" if failure == "audience" else "oaiapp_test",
                           "exp": time.time()-60 if failure == "expiry" else time.time()+60, "iat": time.time(),
                           "sub": "user", "nonce": "bad" if failure == "nonce" else query["nonce"]}, key, algorithm="RS256")
    monkeypatch.setattr(chatgpt, "_token", lambda _: {"id_token": identity, "access_token": "private", "scope": "chatgpt.tokens.use.direct"})
    if failure == "canceled":
        chatgpt.disconnect(accounts, 1)
    handler.path = f'/auth/callback?state={query["state"]}&code=ok&client_id=oaiapp_test' + ("&error=access_denied" if failure == "denied" else "")
    handler.do_GET()
    assert not chatgpt.status(accounts, 1)["sharing"]


def test_refresh_rotation_permission_and_cost(tmp_path, monkeypatch):
    sign_in()
    accounts = Accounts(tmp_path)
    accounts.set_credential(1, "chatgpt", "oauth", {"client_id": "oaiapp_test", "refresh_token": "old",
        "access_token": "expired", "expires_at": 0, "scopes": ["chatgpt.tokens.use.direct"]})
    monkeypatch.setattr(chatgpt, "_token", lambda data: {"access_token": "new", "refresh_token": "rotated", "expires_in": 3600})
    assert chatgpt.Client(accounts, 1).access_token() == "new"
    assert accounts.credential(1, "chatgpt")["refresh_token"] == "rotated"
    assert settings.call_cost("chatgpt/model", {"input_tokens": 20}) is None
    chatgpt.disconnect(accounts, 1)
    with pytest.raises(ValueError, match="enable ChatGPT plan"):
        chatgpt.Client(accounts, 1).access_token()


@pytest.mark.parametrize("terminal", ["response.completed", "response.failed", "response.incomplete", "interrupted"])
def test_stream_requires_completion(tmp_path, monkeypatch, terminal):
    client = chatgpt.Client(Accounts(tmp_path), 1)
    monkeypatch.setattr(client, "access_token", lambda: "private")
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_lines(self):
            yield 'data: {"type":"response.output_text.delta","delta":"partial"}'
            if terminal != "interrupted":
                yield "data: " + json.dumps({"type": terminal, "response": {"output": [
                    {"content": [{"type": "output_text", "text": '{"summary":"ok"}'}]}]}})
    def stream(method, url, **kwargs):
        assert url == chatgpt.RESOURCE + "/responses"
        assert kwargs["json"]["store"] is False and kwargs["json"]["stream"] is True
        return Stream()
    monkeypatch.setattr(chatgpt.httpx, "stream", stream)
    if terminal == "response.completed":
        resp, reply = client.ask("model", "instruction", "text", {}, 100)
        assert reply == {"summary": "ok"} and resp.model == "chatgpt/model"
    else:
        with pytest.raises(ValueError): client.ask("model", "instruction", "text", {}, 100)


def test_provider_uses_existing_guardrails(monkeypatch):
    client = SimpleNamespace(ask=lambda *args: (SimpleNamespace(model="chatgpt/test", usage=None, stop_reason="end_turn"), {"bad": 999}))
    monkeypatch.setattr(coach, "check", lambda *args: ["invented number"])
    result = coach.run("brief", {}, {"safe": True}, client, "chatgpt/test")
    assert result.source == "rules" and result.fallback_reason == "rejected" and result.attempts == 2


def test_model_selection_and_provider_ready(tmp_path, monkeypatch):
    client = sign_in()
    accounts = Accounts(tmp_path)
    accounts.set_credential(1, "chatgpt", "oauth", {"access_token": "private", "scopes": ["chatgpt.tokens.use.direct"]})
    monkeypatch.setattr(chatgpt.Client, "models", lambda _: [{"id": "test-model", "label": "Test"}])
    body = {"enabled": True, "provider": "chatgpt", "chatgpt_model": "unknown"}
    assert client.put("/api/settings/coach", json=body).status_code == 422
    body["chatgpt_model"] = "test-model"
    assert client.put("/api/settings/coach", json=body).status_code == 200
    config = client.get("/api/settings").json()["coach"]
    assert config["provider"] == "chatgpt" and config["brief_model"] == "chatgpt/test-model"
    assert "private" not in json.dumps(config)
    assert client.delete("/api/settings/chatgpt").status_code == 200
    assert not client.get("/api/settings").json()["coach"]["key_set"]


def test_model_catalog_uses_visible_account_models(tmp_path, monkeypatch):
    client = chatgpt.Client(Accounts(tmp_path), 1)
    monkeypatch.setattr(client, "access_token", lambda: "private")
    def get(url, **kwargs):
        assert url == chatgpt.RESOURCE + "/models"
        assert kwargs["headers"]["Authorization"] == "Bearer private"
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"models": [
            {"slug": "available", "display_name": "Available", "visibility": "list"},
            {"slug": "hidden", "visibility": "hidden"}]})
    monkeypatch.setattr(chatgpt.httpx, "get", get)
    assert client.models() == [{"id": "available", "label": "Available"}]
    monkeypatch.setenv("TALUS_CHATGPT_LOCAL", "0")
    with pytest.raises(ValueError, match="local engine"):
        chatgpt.start(Accounts(tmp_path), 1)
