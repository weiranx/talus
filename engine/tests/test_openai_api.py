import json
import httpx
import pytest

from talus import accounts, api, coach, openai_api, settings
from tests.conftest import sign_in
from tests.test_coach import BAD, FALLBACK, GOOD, PACKET, FakeClient


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TALUS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TALUS_COACH_MODEL", raising=False)
    monkeypatch.setattr(api, "load_env", lambda: None)


def response(reply=GOOD, **fields):
    return {"status": "completed", "model": "gpt-6-luna", "usage": {"input_tokens": 900, "output_tokens": 120},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}], **fields}


def stub(monkeypatch, *responses):
    calls, pending = [], list(responses)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-private-key")
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json=pending.pop(0), request=httpx.Request("POST", url))
    monkeypatch.setattr(openai_api.httpx, "post", post)
    return calls


def test_structured_request_uses_key_without_storing_training_context(monkeypatch):
    calls = stub(monkeypatch, response())
    result = coach.run("brief", PACKET, FALLBACK, openai_api.Client(), "gpt-6-luna")
    assert result.source == "coach" and result.reply == GOOD
    url, request = calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert request["headers"]["Authorization"] == "Bearer test-only-private-key"
    body = request["json"]
    assert body["model"] == "gpt-6-luna" and body["store"] is False
    assert body["text"]["format"] == {"type": "json_schema", "name": "coach_reply", "strict": True,
                                       "schema": coach.BRIEF_SCHEMA}
    assert body["reasoning"] == {"effort": "low"} and body["max_output_tokens"] == coach.MODES["brief"]["max_tokens"]
    assert "test-only-private-key" not in json.dumps(body)
    assert result.usage["input_tokens"] == 900 and result.usage["output_tokens"] == 120
    assert settings.call_cost(result.model, result.usage)["usd"] == 0.00015


def test_openai_retries_use_existing_number_guardrails_and_log_all_usage(monkeypatch):
    calls = stub(monkeypatch, response(BAD), response(GOOD))
    result = coach.run("brief", PACKET, FALLBACK, openai_api.Client(), "gpt-6-luna")
    assert result.source == "coach" and result.attempts == 2 and result.rejected
    assert result.usage["input_tokens"] == 1800
    assert "last reply was rejected" in calls[1][1]["json"]["input"]
    stub(monkeypatch, response(BAD), response(BAD))
    result = coach.run("brief", PACKET, FALLBACK, openai_api.Client(), "gpt-6-luna")
    assert result.source == "rules" and result.fallback_reason == "rejected"


@pytest.mark.parametrize("fields,reason", [
    ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "max_tokens"),
    ({"status": "failed"}, "api_error"),
    ({"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]}, "refusal"),
    ({"output": []}, "api_error: ValueError"),
])
def test_unfinished_refused_or_empty_output_falls_back(monkeypatch, fields, reason):
    stub(monkeypatch, response(**fields))
    result = coach.run("brief", PACKET, FALLBACK, openai_api.Client(), "gpt-6-luna")
    assert result.source == "rules" and result.reply == FALLBACK and result.fallback_reason == reason


def test_network_failures_do_not_expose_key_or_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-private-key")
    def fail(*a, **kw):
        raise httpx.ConnectError("sensitive request details")
    monkeypatch.setattr(openai_api.httpx, "post", fail)
    result = coach.run("brief", PACKET, FALLBACK, openai_api.Client(), "gpt-6-luna")
    assert result.fallback_reason == "api_error: ValueError"
    assert "sensitive" not in repr(result) and "private-key" not in repr(result)


def test_catalog_and_routing_allow_mixed_providers_and_ignore_old_signin(monkeypatch):
    client = sign_in()
    store, accounts = api.get_store(), api.get_accounts()
    models = {"brief_model": "gpt-6-luna", "analysis_model": "claude-opus-5-5", "plan_model": "gpt-6.1-sol"}
    assert client.put("/api/admin/coach-models", json=models).status_code == 404  # owner is not automatically admin
    accounts.set_config("coach_models", models)
    store.save_settings({"ai_provider": "chatgpt", "chatgpt_model": "old-subscription-model"})
    calls = stub(monkeypatch, response(), response({"approach": "Build gradually.", "weeks": []}, model="gpt-6.1-sol"))
    monkeypatch.setattr(api, "openai_client", openai_api.default_client)
    claude = FakeClient({field: "Fine." for field in coach.ANALYSIS_FIELDS})
    monkeypatch.setattr(api, "coach_client", lambda: claude)
    assert api._coach(store, "brief", "today", PACKET, FALLBACK, False)["source"] == "coach"
    assert api._coach(store, "analysis", "activity", {}, {}, False)["source"] == "coach"
    # Plan readiness follows the plan model's provider, independently of the brief and analysis.
    assert api._ai_builds(store)
    assert isinstance(api._provider_client(store, models["plan_model"]), openai_api.Client)
    assert api._coach(store, "build", "draft", {}, {}, False)["source"] == "coach"
    assert len(calls) == 2 and len(claude.calls) == 1
    assert calls[1][1]["json"]["model"] == "gpt-6.1-sol"
    assert calls[1][1]["json"]["reasoning"]["effort"] == "medium"
    config = client.get("/api/settings").json()["coach"]
    assert {k: config[k] for k in models} == models
    assert "chatgpt" not in config and "provider" not in config
    assert {"gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"} <= {m["id"] for m in config["models"]}
    assert not config["key_set"]  # mixed selections also need an Anthropic key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-anthropic")
    config = client.get("/api/settings")
    assert config.json()["coach"]["key_set"] and "test-only" not in config.text
    assert client.post("/api/settings/chatgpt/connect").status_code == 404


def test_openai_respects_cap_and_off_even_with_legacy_signin_settings(monkeypatch):
    sign_in()
    store = api.get_store()
    api.get_accounts().set_config("coach_models", {"brief_model": "gpt-6-luna"})
    store.save_settings({"coach_monthly_cap_usd": 0, "ai_provider": "chatgpt"})
    calls = stub(monkeypatch, response())
    monkeypatch.setattr(api, "openai_client", openai_api.default_client)
    assert api._coach(store, "brief", "today", PACKET, FALLBACK, False)["fallback_reason"] == "cap"
    store.save_settings({"coach_enabled": False})
    assert api._coach(store, "brief", "today", PACKET, FALLBACK, False)["fallback_reason"] == "off"
    assert calls == []


def test_missing_openai_key_does_not_use_anthropic(monkeypatch):
    sign_in()
    api.get_accounts().set_config("coach_models", {"brief_model": "gpt-6-luna"})
    monkeypatch.setattr(api, "openai_client", openai_api.default_client)
    def forbidden():
        raise AssertionError("Wrong provider")
    monkeypatch.setattr(api, "coach_client", forbidden)
    result = api._coach(api.get_store(), "brief", "today", PACKET, FALLBACK, False)
    assert result["fallback_reason"] == "no_key"


def test_historical_plan_usage_is_not_billed_as_api_usage():
    assert settings.call_cost("chatgpt/old-model", {"input_tokens": 900, "output_tokens": 120}) is None
    assert settings._priced_as("gpt-6.1-sol-2026-10-01") == "gpt-6.1-sol"


def test_admin_can_save_openai_models_and_others_cannot(monkeypatch):
    monkeypatch.setattr(accounts, "ADMIN_EMAIL", "admin@example.com")
    admin = sign_in("admin@example.com", "a long admin password")
    choices = {"brief_model": "gpt-6-luna", "analysis_model": "claude-opus-5-5", "plan_model": "gpt-6.1-sol"}
    assert admin.put("/api/admin/coach-models", json=choices).json() == choices
    friend = sign_in("friend@example.com", "a long friend password")
    assert friend.put("/api/admin/coach-models", json=choices).status_code == 404
    overview = admin.get("/api/admin").json()
    assert overview["coach_models"]["plan_model"] == "gpt-6.1-sol"
    assert set(overview["coach_models"]["estimate"]["plan"]) >= set(choices.values())
    assert friend.get("/api/settings").json()["coach"]["brief_model"] == "gpt-6-luna"

    # The server status must follow the selected provider too, rather than testing only Anthropic.
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-private-key")
    choices["analysis_model"] = "gpt-6.1-terra"
    assert admin.put("/api/admin/coach-models", json=choices).status_code == 200
    status = admin.get("/api/status").json()
    assert status["coach"]["key_set"] and status["server"]["coach"]["key_set"]
    assert status["server"]["coach"]["analysis_model"] == "gpt-6.1-terra"
