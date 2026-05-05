"""Tests for the Anthropic SDK wrapper (ANAL-01 + CP2 cache_control mitigation)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from ls_equity_fund.analysis.claude_client import (
    ClaudeClient,
    estimate_cost,
    load_prompt,
    parse_json,
)
from ls_equity_fund.analysis.cost_tracker import CostCeilingExceeded, CostTracker


# --- _build_system: the load-bearing CP2 invariant ----------------------------


def _make_client(*, use_cache_control: bool = True, cache_ttl: str | None = "1h") -> ClaudeClient:
    return ClaudeClient(
        api_key="fake-key",
        model="claude-sonnet-4-5",
        cost_tracker=CostTracker(),
        use_cache_control=use_cache_control,
        cache_ttl=cache_ttl,
    )


def test_system_is_a_list_of_content_blocks_not_string() -> None:
    """CP2 — without this, prompt-cache silently disables and the budget blows."""
    c = _make_client()
    blocks = c._build_system(["instructions", "schema"])
    assert isinstance(blocks, list), "system MUST be a list, not a string"
    assert all(isinstance(b, dict) for b in blocks)
    assert blocks[0]["type"] == "text"
    assert blocks[0]["text"] == "instructions"


def test_each_system_block_has_cache_control() -> None:
    c = _make_client()
    blocks = c._build_system(["a", "b", "c"])
    for b in blocks:
        assert "cache_control" in b
        assert b["cache_control"]["type"] == "ephemeral"


def test_cache_ttl_1h_is_passed_to_cache_control() -> None:
    c = _make_client(cache_ttl="1h")
    blocks = c._build_system(["x"])
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_cache_ttl_none_uses_5min_default() -> None:
    c = _make_client(cache_ttl=None)
    blocks = c._build_system(["x"])
    # 5min default = no explicit ttl marker
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_use_cache_control_false_falls_back_to_string() -> None:
    """Debugging escape hatch — production path NEVER uses this."""
    c = _make_client(use_cache_control=False)
    out = c._build_system(["a", "b"])
    assert isinstance(out, str)
    assert "a" in out and "b" in out


def test_empty_blocks_raises() -> None:
    c = _make_client()
    with pytest.raises(ValueError):
        c._build_system([])


# --- call() with a mocked SDK -------------------------------------------------


def _mock_response(
    text: str = "{}",
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_write: int = 0,
    cache_read: int = 0,
) -> MagicMock:
    """Construct a mock matching the Anthropic Message + Usage shape."""
    text_block = MagicMock()
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    response.usage.cache_creation_input_tokens = cache_write
    response.usage.cache_read_input_tokens = cache_read
    return response


def test_call_records_usage_and_returns_text() -> None:
    c = _make_client()
    c._client.messages.create = MagicMock(return_value=_mock_response("hello"))
    resp = c.call(system_blocks=["hi"], user_message="test")
    assert resp.text == "hello"
    assert c.cost_tracker.input_tokens == 100
    assert c.cost_tracker.output_tokens == 50


def test_call_aborts_when_cost_ceiling_already_hit() -> None:
    c = _make_client()
    c.cost_tracker = CostTracker(ceiling_usd=0.0001)
    c.cost_tracker.record({"input_tokens": 1000, "output_tokens": 0})  # busts budget
    c._client.messages.create = MagicMock()
    with pytest.raises(CostCeilingExceeded):
        c.call(system_blocks=["x"], user_message="y")
    # The SDK was never called
    c._client.messages.create.assert_not_called()


def test_call_passes_cache_control_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trip the CP2 invariant — the wrapper sends ``system`` as a list."""
    c = _make_client()
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _mock_response("{}")

    c._client.messages.create = fake_create
    c.call(system_blocks=["sys-1", "sys-2"], user_message="usr")

    assert isinstance(captured["system"], list), "CP2 violation"
    assert all("cache_control" in b for b in captured["system"])


# --- parse_json ---------------------------------------------------------------


def test_parse_json_whole_response() -> None:
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_fenced_block() -> None:
    response = "Here's the analysis:\n```json\n{\"score\": 75}\n```\nDone."
    assert parse_json(response) == {"score": 75}


def test_parse_json_bare_object() -> None:
    response = "Sure! {\"a\": 2}"
    assert parse_json(response) == {"a": 2}


def test_parse_json_raises_on_no_json() -> None:
    with pytest.raises(ValueError):
        parse_json("no objects here at all")


def test_parse_json_raises_on_empty() -> None:
    with pytest.raises(ValueError):
        parse_json("   ")


# --- prompt loading -----------------------------------------------------------


def test_load_prompt_v1_filing_exists() -> None:
    text = load_prompt("filing", version="v1")
    assert "earnings_quality_score" in text
    # Stable instruction guards: no datetime.now() / per-request values
    assert "now()" not in text.lower()


def test_load_prompt_v1_risk_has_severity() -> None:
    text = load_prompt("risk", version="v1")
    assert "risk_severity" in text
    assert "boilerplate_percentage" in text


def test_load_prompt_v1_insider_lists_form4_codes() -> None:
    text = load_prompt("insider", version="v1")
    for code in ("P", "S", "A", "M", "F", "G", "D"):
        assert f" {code} " in text or f"= {code}" in text


def test_load_prompt_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt("definitely-nonexistent")


# --- cost estimator -----------------------------------------------------------


def test_estimate_cost_handles_cache_chars() -> None:
    """Warm-cache estimate: cache_chars billed at 0.1× input rate."""
    no_cache = estimate_cost(input_chars=4000, output_chars=400)
    warm = estimate_cost(input_chars=4000, output_chars=400, cache_chars=4000)
    assert warm < no_cache
