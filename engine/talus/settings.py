"""App settings kept in SQLite and edited from the web app's Settings screen.

API keys are not settings: they stay in .env, and the engine only ever reports whether one is set.
"""

from __future__ import annotations

from . import coach

# Per-MTok prices, input and output. Haiku has no server-side fallback, so it runs without it.
MODELS = {
    "claude-opus-5-5": {"label": "Opus 5.5", "input": 4.00, "output": 20.00, "fallback": True},
    "claude-sonnet-5-5": {"label": "Sonnet 5.5", "input": 2.00, "output": 10.00, "fallback": True},
    "claude-haiku-5-5": {"label": "Haiku 5.5", "input": 0.10, "output": 0.50, "fallback": False},
}

# Typical tokens per call, used for the cost estimate until the coach log has real usage.
TYPICAL_TOKENS = {"brief": (2500, 500), "analysis": (5000, 1000)}

COACH_KEYS = ("coach_enabled", "brief_model", "analysis_model", "coach_style")


def defaults() -> dict:
    return {"coach_enabled": True, "brief_model": coach.default_model("brief"),
            "analysis_model": coach.default_model("analysis"), "coach_style": coach.DEFAULT_STYLE}


def cost_per_call(model: str, tokens: tuple[float, float]) -> float | None:
    m = MODELS.get(model)
    if m is None:
        return None
    return (tokens[0] * m["input"] + tokens[1] * m["output"]) / 1e6
