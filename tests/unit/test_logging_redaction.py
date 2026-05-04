"""Tests for ls_equity_fund.logging — redaction, run_id binding, dual sink, stdlib bridge.

Each test maps to a `<must_haves>.truths` entry in 00-04-PLAN.md and to a locked
decision in 00-CONTEXT.md (D-16 .. D-20).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import uuid
from pathlib import Path

import pytest
import structlog

from ls_equity_fund.config import LoggingConfig
from ls_equity_fund.logging import (
    DEFAULT_REDACT_KEYS,
    REDACT_PATTERNS,
    REDACTED_PLACEHOLDER,
    bind_run_id,
    clear_run_id,
    configure_logging,
    redaction_processor,
)


def _today_utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


def _flush_root_handlers() -> None:
    for h in logging.getLogger().handlers:
        h.flush()


@pytest.fixture(autouse=True)
def reset_logging(monkeypatch: pytest.MonkeyPatch):
    """Reset module guard + root handlers + contextvars between tests."""
    import ls_equity_fund.logging as logging_mod

    monkeypatch.setattr(logging_mod, "_CONFIGURED", False)
    root = logging.getLogger()
    # Remove and close any handlers from prior tests so file locks release
    # cleanly under tmp_path teardown.
    for h in list(root.handlers):
        with contextlib.suppress(Exception):
            h.close()
        root.removeHandler(h)
    clear_run_id()
    yield
    # Teardown: same dance.
    for h in list(root.handlers):
        with contextlib.suppress(Exception):
            h.close()
        root.removeHandler(h)
    clear_run_id()
    # Reset structlog to defaults so other test files aren't affected.
    structlog.reset_defaults()


# ---------------------------------------------------------------------------
# Truth 1 (D-18): allowlist-by-key redaction
# ---------------------------------------------------------------------------
def test_allowlist_redaction_by_key() -> None:
    out = redaction_processor(None, "info", {"api_key": "anything-here", "user": "alice"})
    assert out["api_key"] == REDACTED_PLACEHOLDER
    assert out["user"] == "alice"


def test_allowlist_is_case_insensitive() -> None:
    out = redaction_processor(None, "info", {"API_KEY": "leak", "Token": "leak2"})
    assert out["API_KEY"] == REDACTED_PLACEHOLDER
    assert out["Token"] == REDACTED_PLACEHOLDER


# ---------------------------------------------------------------------------
# Truth 2 (D-18): regex on string values for non-allowlisted keys
# ---------------------------------------------------------------------------
def test_regex_redaction_on_string_value() -> None:
    out = redaction_processor(None, "info", {"message": "got token: sk-ant-FAKEKEY12345"})
    assert "sk-ant-FAKEKEY12345" not in out["message"]
    assert REDACTED_PLACEHOLDER in out["message"]


def test_bearer_token_regex() -> None:
    out = redaction_processor(None, "info", {"headers": "Authorization: Bearer abc.DEF-123"})
    assert "Bearer abc.DEF-123" not in out["headers"]
    assert REDACTED_PLACEHOLDER in out["headers"]


# ---------------------------------------------------------------------------
# Truth 8 (D-18): UUIDs and order_ids are NOT redacted
# ---------------------------------------------------------------------------
def test_uuid_not_redacted() -> None:
    """UUID values pass through (no generic alnum-32+ regex per D-18)."""
    run_id = "550e8400-e29b-41d4-a716-446655440000"
    out = redaction_processor(None, "info", {"event": "started", "run_id": run_id})
    assert out["run_id"] == run_id


def test_order_id_not_redacted() -> None:
    """32-char hex order id survives (not an allowlist key, no matching regex)."""
    order_id = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    out = redaction_processor(None, "info", {"oid": order_id})
    assert out["oid"] == order_id


def test_redact_patterns_does_not_include_generic_random() -> None:
    """Sanity: REDACT_PATTERNS contains no generic alnum-{N,} pattern."""
    for pat in REDACT_PATTERNS:
        assert "{32,}" not in pat.pattern, pat.pattern
        assert "{16,}" not in pat.pattern, pat.pattern
        assert "{8,}" not in pat.pattern, pat.pattern


def test_default_redact_keys_set() -> None:
    """D-18 allowlist is exactly the documented 9 keys."""
    assert set(DEFAULT_REDACT_KEYS) == {
        "api_key",
        "apikey",
        "password",
        "passwd",
        "token",
        "secret",
        "authorization",
        "auth",
        "key",
    }


def test_redact_patterns_count() -> None:
    """D-18: exactly two regex patterns (sk-ant-, Bearer )."""
    assert len(REDACT_PATTERNS) == 2


# ---------------------------------------------------------------------------
# Truth 7 (D-20): configure_logging is the single config point + idempotent
# ---------------------------------------------------------------------------
def test_configure_logging_idempotent(tmp_path: Path) -> None:
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)
    handlers_first = list(logging.getLogger().handlers)
    configure_logging(cfg)  # second call must NOT double handlers
    handlers_second = list(logging.getLogger().handlers)
    assert len(handlers_first) == len(handlers_second)


# ---------------------------------------------------------------------------
# Truth 4 (D-19): run_id appears in log lines after bind_run_id
# ---------------------------------------------------------------------------
def test_run_id_appears_in_log(tmp_path: Path) -> None:
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    rid = str(uuid.uuid4())
    bind_run_id(rid)

    structlog.get_logger("test").info("test_event", x=1)
    _flush_root_handlers()

    log_file = tmp_path / f"{_today_utc()}.jsonl"
    text = log_file.read_text(encoding="utf-8")
    assert rid in text, f"run_id {rid} not found in log file: {text!r}"


def test_run_id_accepts_uuid_object(tmp_path: Path) -> None:
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    rid_obj = uuid.uuid4()
    bind_run_id(rid_obj)

    structlog.get_logger("test").info("ev")
    _flush_root_handlers()

    log_file = tmp_path / f"{_today_utc()}.jsonl"
    assert str(rid_obj) in log_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Truth 6 (D-17): dual sink — file at logs/{UTC-YYYY-MM-DD}.jsonl
# ---------------------------------------------------------------------------
def test_dual_sink_writes_to_file(tmp_path: Path) -> None:
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    structlog.get_logger("dual_sink_test").info("hello_file", marker="WRITE_ME_TO_FILE")
    _flush_root_handlers()

    log_file = tmp_path / f"{_today_utc()}.jsonl"
    assert log_file.exists(), f"Log file {log_file} not created"
    assert "WRITE_ME_TO_FILE" in log_file.read_text(encoding="utf-8")


def test_file_sink_redacts(tmp_path: Path) -> None:
    """File sink applies redaction (D-18 + D-17)."""
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    structlog.get_logger("file_redact_test").info(
        "api_call", api_key="sk-ant-SHOULD-BE-GONE", user="alice"
    )
    _flush_root_handlers()

    text = (tmp_path / f"{_today_utc()}.jsonl").read_text(encoding="utf-8")
    assert "sk-ant-SHOULD-BE-GONE" not in text, "API key leaked into log file"
    assert REDACTED_PLACEHOLDER in text


def test_file_sink_is_jsonl(tmp_path: Path) -> None:
    """Each line in the file is a JSON object (D-17 — auditability via jq)."""
    import json

    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    log = structlog.get_logger("jsonl_test")
    log.info("e1", n=1)
    log.info("e2", n=2)
    _flush_root_handlers()

    text = (tmp_path / f"{_today_utc()}.jsonl").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) >= 2
    for line in lines:
        parsed = json.loads(line)
        assert "event" in parsed


# ---------------------------------------------------------------------------
# Truth 5 (D-16): renderer choice
# ---------------------------------------------------------------------------
def test_file_sink_uses_json_regardless_of_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when stderr.isatty() is True, file always writes JSON (D-17)."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    structlog.get_logger("isatty_true").info("tty_event", who="op")
    _flush_root_handlers()

    text = (tmp_path / f"{_today_utc()}.jsonl").read_text(encoding="utf-8")
    # JSON line begins with `{` and ends (before newline) with `}`.
    first_nonempty = next(ln for ln in text.splitlines() if ln.strip())
    assert first_nonempty.startswith("{") and first_nonempty.rstrip().endswith("}"), (
        f"File line is not JSON when isatty=True: {first_nonempty!r}"
    )


def test_renderer_selection_when_non_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When stderr is NOT a TTY, stderr handler also uses JSON (D-16)."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    # Inspect the stderr handler formatter's processor.
    stream_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers, "no stderr StreamHandler attached"
    formatter = stream_handlers[0].formatter
    # ProcessorFormatter stores the chain in .processors — the renderer is the
    # final element in the tuple. (structlog>=22 API)
    procs = getattr(formatter, "processors", None) or (getattr(formatter, "processor", None),)
    renderer = procs[-1]
    assert isinstance(renderer, structlog.processors.JSONRenderer), (
        f"Expected JSONRenderer when isatty=False, got {type(renderer).__name__}"
    )


def test_renderer_selection_when_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When stderr IS a TTY, stderr handler uses ConsoleRenderer (D-16)."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)

    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    stream_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers, "no stderr StreamHandler attached"
    formatter = stream_handlers[0].formatter
    procs = getattr(formatter, "processors", None) or (getattr(formatter, "processor", None),)
    renderer = procs[-1]
    assert isinstance(renderer, structlog.dev.ConsoleRenderer), (
        f"Expected ConsoleRenderer when isatty=True, got {type(renderer).__name__}"
    )


# ---------------------------------------------------------------------------
# Truth 3 (D-20): stdlib bridge — third-party logs flow through redaction
# ---------------------------------------------------------------------------
def test_stdlib_bridge_redacts(tmp_path: Path) -> None:
    """logging.getLogger('anthropic').error(...) flows through structlog redaction."""
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    third_party = logging.getLogger("anthropic")
    third_party.error("request failed: api_key=sk-ant-LEAKY-FROM-STDLIB context=abc")
    _flush_root_handlers()

    text = (tmp_path / f"{_today_utc()}.jsonl").read_text(encoding="utf-8")
    assert "sk-ant-LEAKY-FROM-STDLIB" not in text, "stdlib log leaked API key"
    assert REDACTED_PLACEHOLDER in text


def test_stdlib_bridge_carries_logger_name(tmp_path: Path) -> None:
    """Stdlib logs reach the file pipeline AND are tagged with their logger name."""
    cfg = LoggingConfig(level="INFO", log_dir=str(tmp_path))
    configure_logging(cfg)

    logging.getLogger("ib_async").warning("connection retry %d", 3)
    _flush_root_handlers()

    text = (tmp_path / f"{_today_utc()}.jsonl").read_text(encoding="utf-8")
    assert "ib_async" in text
    assert "connection retry 3" in text
