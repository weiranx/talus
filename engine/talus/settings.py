"""App settings kept in SQLite and edited from the web app's Settings screen.

Each account has its own settings, in its own talus.db. Keys are not settings: the Anthropic key
stays in .env (the coach runs on the server's key), and each account's intervals.icu key lives
encrypted in accounts.db. The engine only ever reports whether a key is set.
"""

from __future__ import annotations

import json
import os

from . import coach

# Per-MTok prices, input and output. Haiku has no server-side fallback, so it runs without it.
MODELS = {
    "claude-opus-5-5": {"label": "Opus 5.5", "input": 4.00, "output": 20.00, "fallback": True},
    "claude-sonnet-5-5": {"label": "Sonnet 5.5", "input": 2.00, "output": 10.00, "fallback": True},
    "claude-haiku-5-5": {"label": "Haiku 5.5", "input": 0.10, "output": 0.50, "fallback": False},
}

# Typical tokens per call, used for the cost estimate until the coach log has real usage.
TYPICAL_TOKENS = {"brief": (2500, 500), "analysis": (5000, 1000), "build": (12000, 6000), "check": (16000, 4000)}

COACH_KEYS = ("coach_enabled", "brief_model", "analysis_model", "coach_style")


def defaults() -> dict:
    return {"coach_enabled": True, "brief_model": coach.default_model("brief"),
            "analysis_model": coach.default_model("analysis"), "coach_style": coach.DEFAULT_STYLE}


def cost_per_call(model: str, tokens: tuple[float, float]) -> float | None:
    m = MODELS.get(model)
    if m is None:
        return None
    return (tokens[0] * m["input"] + tokens[1] * m["output"]) / 1e6


def _priced_as(model: str | None) -> str:
    """The price list entry for a logged model. The API can answer with a dated id or, with the
    server-side fallback, another model; anything not recognised is priced as the dearest, so the
    cap errs on the safe side."""
    for name in MODELS:
        if model and (model == name or model.startswith(name + "-")):
            return name
    return max(MODELS, key=lambda m: MODELS[m]["output"])


def server_ceiling() -> float:
    """Most an account other than the owner's can spend on coach calls each month, in USD."""
    try:
        return float(os.environ.get("TALUS_COACH_CAP_USD", "5"))
    except ValueError:
        return 5.0


def call_cost(model: str | None, usage: dict | str | None) -> dict | None:
    """What one logged coach call cost, from its token usage (a retry's tokens included). Cache
    reads are priced as plain input, so this errs high. None when the call never reached the API."""
    if isinstance(usage, str):
        usage = json.loads(usage)
    if not usage:
        return None
    tokens = (sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                              "cache_creation_input_tokens")), usage.get("output_tokens") or 0)
    return {"usd": round(cost_per_call(_priced_as(model), tokens), 6), "model": model,
            "input_tokens": tokens[0], "output_tokens": tokens[1]}


def spent_since(store, since) -> float:
    """Coach spending since a day, from the usage logged with each call."""
    total = 0.0
    for r in store.db.execute("SELECT model, usage FROM coach_log WHERE created_at >= ?"
                              " AND usage IS NOT NULL AND usage != 'null'", (since.isoformat(),)):
        total += call_cost(r["model"], r["usage"])["usd"]
    return round(total, 4)
