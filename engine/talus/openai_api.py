"""OpenAI API coach adapter. The key stays server-side and never reaches the UI."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import httpx


def default_client(api_key: str | None = None) -> Client | None:
    key = api_key or os.environ.get("OPENAI_API_KEY")
    return Client(key) if key else None


class Client:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")

    def ask(self, model: str, system: str, text: str, schema: dict, max_tokens: int,
            effort: str = "low") -> tuple[SimpleNamespace, dict | None]:
        try:
            if not self._api_key:
                raise ValueError("OpenAI key not configured.")
            response = httpx.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + self._api_key},
                json={"model": model, "instructions": system, "input": text, "store": False,
                      "max_output_tokens": max_tokens, "reasoning": {"effort": effort},
                      "text": {"format": {"type": "json_schema", "name": "coach_reply",
                                          "strict": True, "schema": schema}}},
                timeout=180,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # HTTP exceptions can contain request details. Only a safe error reaches the coach log.
            raise ValueError("OpenAI request failed.") from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid OpenAI response.")
        usage = data.get("usage") or {}
        result = SimpleNamespace(model=data.get("model") or model,
                                 usage=SimpleNamespace(input_tokens=usage.get("input_tokens", 0),
                                                       output_tokens=usage.get("output_tokens", 0)),
                                 stop_reason="end_turn")
        if data.get("status") != "completed":
            result.stop_reason = ("max_tokens" if (data.get("incomplete_details") or {}).get("reason")
                                  == "max_output_tokens" else "api_error")
            return result, None
        content = [part for item in data.get("output", []) if item.get("type") == "message"
                   for part in item.get("content", [])]
        if any(part.get("type") == "refusal" for part in content):
            result.stop_reason = "refusal"
            return result, None
        body = "".join(part.get("text", "") for part in content if part.get("type") == "output_text")
        try:
            reply = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid OpenAI coach reply.") from exc
        if not isinstance(reply, dict):
            raise ValueError("Invalid OpenAI coach reply.")
        return result, reply
