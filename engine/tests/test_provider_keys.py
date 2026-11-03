import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from talus import accounts as account_module, api, coach, openai_api, provider_keys
from talus.accounts import Accounts
from tests.conftest import sign_in

ADMIN = {"email": "admin@example.com", "password": "a long admin password"}
FRIEND = {"email": "friend@example.com", "password": "a long friend password"}
KEY = "sk-test-admin-provider-key-123456789"
ENV_KEY = "sk-test-environment-key-123456789"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TALUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("TALUS_COACH_MODEL", raising=False)
    monkeypatch.setattr(api, "load_env", lambda: None)
    monkeypatch.setattr(account_module, "ADMIN_EMAIL", ADMIN["email"])


def test_only_admin_can_change_shared_provider_keys():
    anonymous = TestClient(api.app)
    owner = sign_in()  # Owning the installation does not grant admin access.
    friend = sign_in(**FRIEND)
    admin = sign_in(**ADMIN)
    for client, status in ((anonymous, 401), (owner, 404), (friend, 404)):
        assert client.put("/api/admin/coach-keys/openai", json={"api_key": KEY}).status_code == status
        assert client.delete("/api/admin/coach-keys/openai").status_code == status
    assert admin.put("/api/admin/coach-keys/openai", json={"api_key": KEY}).status_code == 200
    assert admin.put("/api/admin/coach-keys/unknown", json={"api_key": KEY}).status_code == 404
    assert admin.delete("/api/admin/coach-keys/unknown").status_code == 404


@pytest.mark.parametrize("provider,env", [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
def test_keys_are_encrypted_persistent_and_write_only(tmp_path, monkeypatch, provider, env):
    admin = sign_in(**ADMIN)
    monkeypatch.setenv(env, ENV_KEY)
    before = next(p for p in admin.get("/api/admin").json()["coach_models"]["providers"] if p["id"] == provider)
    assert before["source"] == "environment" and not before["saved_key_set"]
    response = admin.put(f"/api/admin/coach-keys/{provider}", json={"api_key": f"  {KEY}  "})
    assert response.status_code == 200
    assert response.json()["source"] == "admin" and response.json()["environment_key_set"]
    assert response.json()["saved_key_set"] and response.json()["updated_at"]
    reopened = Accounts(tmp_path)
    assert provider_keys.saved_key(reopened, provider) == KEY
    assert KEY.encode() not in reopened.db.execute("SELECT secret FROM coach_key").fetchone()[0].encode()
    for database in tmp_path.glob("accounts.db*"):
        assert KEY.encode() not in database.read_bytes()
    overview = admin.get("/api/admin")
    assert KEY not in response.text + overview.text and ENV_KEY not in overview.text
    action = overview.json()["log"][0]
    assert action["action"] == "coach_key_updated" and action["detail"] == {"provider": provider}
    friend = sign_in(**FRIEND)
    assert KEY not in friend.get("/api/settings").text
    assert "providers" not in friend.get("/api/settings").json()["coach"]


@pytest.mark.parametrize("provider,model,env,factory", [
    ("openai", "gpt-6-luna", "OPENAI_API_KEY", "openai_client"),
    ("anthropic", "claude-haiku-5-5", "ANTHROPIC_API_KEY", "coach_client"),
])
def test_rotation_applies_to_new_requests_and_removal_restores_environment(monkeypatch, provider, model, env, factory):
    admin = sign_in(**ADMIN)
    api.get_accounts().set_config("coach_models", {"brief_model": model, "analysis_model": model, "plan_model": model})
    monkeypatch.setenv(env, ENV_KEY)
    seen = []
    def create(api_key=None):
        seen.append(api_key or ENV_KEY)
        return SimpleNamespace()
    monkeypatch.setattr(api, factory, create)
    store = api.get_store()
    api._provider_client(store)
    assert seen[-1] == ENV_KEY
    admin.put(f"/api/admin/coach-keys/{provider}", json={"api_key": KEY})
    api._provider_client(store)
    assert seen[-1] == KEY
    rotated = KEY + "-rotated"
    admin.put(f"/api/admin/coach-keys/{provider}", json={"api_key": rotated})
    api._provider_client(store)
    assert seen[-1] == rotated
    assert admin.get("/api/settings").json()["coach"]["key_set"]
    assert admin.get("/api/status").json()["server"]["coach"]["key_set"]
    cleared = admin.delete(f"/api/admin/coach-keys/{provider}").json()
    assert cleared["source"] == "environment" and not cleared["saved_key_set"]
    api._provider_client(store)
    assert seen[-1] == ENV_KEY
    monkeypatch.delenv(env)
    assert not admin.get("/api/settings").json()["coach"]["key_set"]
    assert admin.get("/api/admin").json()["log"][0]["action"] == "coach_key_removed"


@pytest.mark.parametrize("body", [None, [], {"api_key": [KEY]}, {"api_key": 123},
                                      {"api_key": ""}, {"api_key": KEY + "\nheader"}, {"api_key": "sk-" + "a" * 1024}])
def test_invalid_key_errors_never_echo_the_submitted_value(body):
    admin = sign_in(**ADMIN)
    response = admin.put("/api/admin/coach-keys/openai", content=json.dumps(body), headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert KEY not in response.text
    assert not api.get_accounts().coach_key("openai")
    assert admin.get("/api/admin").json()["log"] == []


def test_malformed_body_is_checked_after_admin_authorization():
    owner = sign_in()
    assert owner.put("/api/admin/coach-keys/openai", content=KEY).status_code == 404
    admin = sign_in(**ADMIN)
    assert admin.put("/api/admin/coach-keys/openai", content=KEY).status_code == 415
    response = admin.put("/api/admin/coach-keys/openai", content=KEY, headers={"content-type": "application/json"})
    assert response.status_code == 422 and KEY not in response.text


def test_unreadable_saved_key_can_be_removed_or_replaced(tmp_path, monkeypatch):
    admin = sign_in(**ADMIN)
    admin.put("/api/admin/coach-keys/openai", json={"api_key": KEY})
    (tmp_path / "secret.key").unlink()
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    status = next(p for p in admin.get("/api/admin").json()["coach_models"]["providers"] if p["id"] == "openai")
    assert status["saved_key_set"] and status["source"] == "environment"
    assert admin.delete("/api/admin/coach-keys/openai").status_code == 200
    assert admin.put("/api/admin/coach-keys/openai", json={"api_key": KEY}).json()["source"] == "admin"


def test_provider_clients_use_explicit_keys_without_mutating_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    monkeypatch.setattr("anthropic.Anthropic", lambda **kwargs: kwargs)
    assert coach.default_client(api_key=KEY) == {"api_key": KEY}
    assert openai_api.default_client(api_key=KEY)._api_key == KEY
    import os
    assert os.environ["OPENAI_API_KEY"] == ENV_KEY and os.environ["ANTHROPIC_API_KEY"] == ENV_KEY
