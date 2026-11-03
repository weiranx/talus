"""Resolve server-wide AI keys without changing process environment or returning secrets to the UI."""
from __future__ import annotations

import os

from .accounts import Accounts

PROVIDERS = {
    "anthropic": {"label": "Anthropic", "env": "ANTHROPIC_API_KEY"},
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY"},
}


def for_model(model: str) -> str:
    return "openai" if model.startswith("gpt-") else "anthropic"


def saved_key(accounts: Accounts, provider: str) -> str | None:
    saved = accounts.coach_key(provider)
    return saved and saved["api_key"]


def available(accounts: Accounts, model: str) -> bool:
    provider = for_model(model)
    return bool(saved_key(accounts, provider) or os.environ.get(PROVIDERS[provider]["env"]))


def status(accounts: Accounts, provider: str) -> dict:
    saved = accounts.coach_key(provider)
    saved_set = bool(saved and saved["api_key"])
    environment_set = bool(os.environ.get(PROVIDERS[provider]["env"]))
    return {"id": provider, "label": PROVIDERS[provider]["label"], "key_set": saved_set or environment_set,
            "source": "admin" if saved_set else "environment" if environment_set else None,
            "saved_key_set": saved is not None, "environment_key_set": environment_set,
            "updated_at": saved["updated_at"] if saved else None}
