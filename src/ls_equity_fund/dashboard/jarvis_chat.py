"""User-action-only JARVIS chat helpers (DASH-09)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SESSION_COST_LIMIT_USD = 1.0
MAX_HISTORY_TURNS = 10


class JarvisClient(Protocol):
    model: str

    def call(self, *, system_blocks: list[str], user_message: str, max_tokens: int = 1500, temperature: float = 0.0) -> object:
        ...


@dataclass(frozen=True)
class ChatResult:
    text: str
    session_cost_usd: float
    disabled: bool


def build_user_message(snapshot_json: str, history: list[dict[str, str]], user_message: str) -> str:
    bounded_history = history[-MAX_HISTORY_TURNS:]
    return json.dumps(
        {
            "snapshot": json.loads(snapshot_json or "{}"),
            "history": bounded_history,
            "question": user_message,
        },
        default=str,
    )


def ask_jarvis(
    *,
    client: JarvisClient,
    snapshot_path: Path,
    history: list[dict[str, str]],
    user_message: str,
    session_cost_usd: float,
) -> ChatResult:
    if session_cost_usd > SESSION_COST_LIMIT_USD:
        return ChatResult("Session limit reached", session_cost_usd, True)
    snapshot_json = snapshot_path.read_text(encoding="utf-8") if snapshot_path.exists() else "{}"
    response = client.call(
        system_blocks=[
            "You are JARVIS, an concise operator assistant. Use the cached system snapshot only.",
            snapshot_json,
        ],
        user_message=build_user_message(snapshot_json, history, user_message),
        max_tokens=900,
    )
    usage = getattr(response, "usage", {}) or {}
    delta = _estimate_usage_cost(usage) if isinstance(usage, dict) else 0.0
    return ChatResult(str(getattr(response, "text", response)), session_cost_usd + delta, False)


def _estimate_usage_cost(usage: dict[str, Any]) -> float:
    input_tokens = float(usage.get("input_tokens", 0) or 0)
    output_tokens = float(usage.get("output_tokens", 0) or 0)
    return input_tokens / 1_000_000 * 3.0 + output_tokens / 1_000_000 * 15.0


__all__ = ["MAX_HISTORY_TURNS", "SESSION_COST_LIMIT_USD", "ChatResult", "ask_jarvis", "build_user_message"]
