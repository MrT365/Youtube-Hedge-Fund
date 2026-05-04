"""structlog configuration with API-key redaction, run-id correlation, dual sink.

Single configuration point for all logging in ls_equity_fund. Every CLI entry
calls :func:`configure_logging` exactly once; every layer obtains loggers via
``structlog.get_logger(__name__)`` and never reconfigures.

Locked decisions implemented here (00-CONTEXT.md):

* **D-16** — renderer is auto-detected via ``sys.stderr.isatty()``. TTY -> a
  colorized :class:`structlog.dev.ConsoleRenderer`; non-TTY (launchd, redirect
  to file, CI) -> :class:`structlog.processors.JSONRenderer`. The decision is
  taken once at :func:`configure_logging` time, not per-event.
* **D-17** — *dual sink*. Every event flows to ``stderr`` AND to a per-day
  rotating file ``{log_dir}/{YYYY-MM-DD}.jsonl`` (UTC date, append mode). The
  file handler ALWAYS writes JSON regardless of TTY — files are the audit
  trail (AUDIT-02) and must be ``jq``-friendly.
* **D-18** — *allowlist + regex* redaction. Keys whose name matches
  :data:`DEFAULT_REDACT_KEYS` (case-insensitive) are blanket-replaced with
  :data:`REDACTED_PLACEHOLDER`. String values for non-allowlisted keys are
  scanned by :data:`REDACT_PATTERNS` (Anthropic ``sk-ant-...`` and HTTP
  ``Bearer ...`` tokens). **No generic alnum-32+ regex** — that would mask
  UUIDs (``run_id``, ``order_id``) and SHA hashes, breaking the audit trail.
* **D-19** — :func:`bind_run_id` wraps
  :func:`structlog.contextvars.bind_contextvars` and is the only public way to
  attach a per-run identifier. Every subsequent log line in the same thread/
  task carries ``run_id=<uuid>``.
* **D-20** — single configuration point. Stdlib :mod:`logging` is bridged via
  :class:`structlog.stdlib.ProcessorFormatter` so third-party libraries
  (anthropic, ib_async, requests, urllib3) emit through the SAME pipeline
  (one redaction policy, one renderer choice).

Public API:
    configure_logging(config)   — idempotent setup; call once at CLI entry
    bind_run_id(run_id)         — bind per-run UUID to contextvars
    clear_run_id()              — clear contextvars (test isolation)
    redaction_processor         — exposed for unit testing in isolation
    DEFAULT_REDACT_KEYS         — the 9-name allowlist (D-18)
    REDACT_PATTERNS             — the 2 explicit regexes (D-18)
    REDACTED_PLACEHOLDER        — ``"***REDACTED***"``
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import logging
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from ls_equity_fund.config import LoggingConfig

# ---------------------------------------------------------------------------
# Public constants — D-18 surface
# ---------------------------------------------------------------------------

#: Per D-18 — allowlist of secret-bearing key names. Values whose key name
#: matches any entry below (case-insensitive) are replaced wholesale with
#: :data:`REDACTED_PLACEHOLDER`. Append project-specific extras via
#: ``LoggingConfig.redact_keys``.
DEFAULT_REDACT_KEYS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "auth",
    "key",
)

REDACTED_PLACEHOLDER: str = "***REDACTED***"

#: Per D-18 — explicit known-secret patterns. **No** generic alnum-32+
#: pattern: that would falsely redact UUIDs (run_id, order_id) and SHA
#: hashes, breaking auditability.
REDACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),  # Anthropic API key
    re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+"),  # HTTP Bearer auth header
]


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# D-20: idempotency guard. Re-entrant calls to ``configure_logging`` are a
# no-op; CLI must remain side-effect-stable when invoked multiple times in
# tests, REPL sessions, or nested wrappers.
_CONFIGURED: bool = False


# ---------------------------------------------------------------------------
# Redaction processor (D-18)
# ---------------------------------------------------------------------------


def redaction_processor(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
    *,
    allowlist_keys: tuple[str, ...] = DEFAULT_REDACT_KEYS,
    patterns: list[re.Pattern[str]] | None = None,
) -> dict[str, Any]:
    """structlog processor — redact secrets from ``event_dict`` in place.

    Two-step pipeline:

    1. **Allowlist by key name (case-insensitive).** Any key whose lowercase
       form is in ``allowlist_keys`` has its value replaced with
       :data:`REDACTED_PLACEHOLDER`, regardless of value type or content.
    2. **Regex on string values.** For every remaining key whose value is a
       ``str``, each pattern in ``patterns`` is applied via :meth:`re.sub`,
       replacing matches with :data:`REDACTED_PLACEHOLDER`. Non-string values
       are left untouched (regex on dict/list values is out of scope; if a
       call site logs a structured object containing a secret, it must use a
       redacted key name — see step 1).

    The processor returns the same ``event_dict`` it received (mutated). This
    matches the structlog processor contract.
    """
    pats = REDACT_PATTERNS if patterns is None else patterns
    lower_allowlist = {k.lower() for k in allowlist_keys}

    for key in list(event_dict.keys()):
        # Step 1 — allowlist match by key name (case-insensitive).
        if key.lower() in lower_allowlist:
            event_dict[key] = REDACTED_PLACEHOLDER
            continue

        # Step 2 — regex pass on string values.
        value = event_dict[key]
        if isinstance(value, str):
            for pattern in pats:
                value = pattern.sub(REDACTED_PLACEHOLDER, value)
            event_dict[key] = value

    return event_dict


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_today() -> str:
    """``YYYY-MM-DD`` for the current UTC date (D-17 — UTC, not local)."""
    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")


def _make_console_renderer() -> structlog.dev.ConsoleRenderer:
    return structlog.dev.ConsoleRenderer(colors=True)


def _make_json_renderer() -> structlog.processors.JSONRenderer:
    return structlog.processors.JSONRenderer()


def _select_stderr_renderer() -> Any:
    """Auto-detect renderer for stderr per D-16."""
    return _make_console_renderer() if sys.stderr.isatty() else _make_json_renderer()


def _shared_pre_chain() -> list[Any]:
    """Processors run on BOTH structlog-native and stdlib-foreign events.

    Order matters:

    1. ``merge_contextvars`` — bring run_id (and any other bound vars) into
       the event dict before anything else.
    2. ``add_log_level`` — promote the call method name to a ``level`` field.
    3. ``TimeStamper`` (UTC ISO-8601) — auditability.
    4. ``add_logger_name`` — record the calling logger's name (esp. for
       foreign/stdlib events whose logger name is the only handle on origin).
    5. ``redaction_processor`` — runs LAST so it sees every key that earlier
       processors merged in.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_logger_name,
        redaction_processor,
    ]


def _build_file_handler(log_dir: Path) -> logging.Handler:
    """Append-mode handler for ``logs/{UTC-YYYY-MM-DD}.jsonl`` (D-17).

    A new ``FileHandler`` is opened per :func:`configure_logging` call. The
    filename is computed once at configure time. For a process whose lifetime
    crosses UTC midnight (rare for the daily-refresh launchd job), the next
    run will open a new file naturally; sub-daily relogins re-call
    ``configure_logging`` and pick up the fresh date.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    file_path = log_dir / f"{_utc_today()}.jsonl"

    handler = logging.FileHandler(filename=str(file_path), mode="a", encoding="utf-8")
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=_make_json_renderer(),  # files: always JSON (D-17)
            foreign_pre_chain=_shared_pre_chain(),
        )
    )
    return handler


def _build_stderr_handler() -> logging.Handler:
    """StreamHandler for stderr with renderer auto-detected per D-16."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=_select_stderr_renderer(),
            foreign_pre_chain=_shared_pre_chain(),
        )
    )
    return handler


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def configure_logging(config: LoggingConfig) -> None:
    """Configure structlog + stdlib logging per project policy.

    *Idempotent.* The second and subsequent calls log a warning through the
    already-configured pipeline and return without re-attaching handlers.

    The ``config.redact_keys`` extension list is currently advisory — at this
    layer the redaction processor is bound to :data:`DEFAULT_REDACT_KEYS`.
    Future plans may surface a configurable allowlist via this field; for
    v1 the allowlist is the locked 9-key set per D-18.
    """
    global _CONFIGURED
    if _CONFIGURED:
        structlog.get_logger(__name__).warning(
            "configure_logging called twice; ignoring second call"
        )
        return

    log_dir = Path(config.log_dir)

    # ---- structlog native pipeline (D-20) -----------------------------------
    # When code calls structlog.get_logger().info(...) directly, the event
    # walks this processor chain and is finally handed to a stdlib formatter
    # via ProcessorFormatter.wrap_for_formatter — converging both code paths
    # onto a single rendering surface (single redaction policy).
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_logger_name,
            redaction_processor,  # type: ignore[list-item]  # default kwargs make this 3-arg-callable
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ---- stdlib bridge (D-20) -----------------------------------------------
    # Wipe pre-existing handlers (third-party libraries occasionally install
    # their own at import time). The two ProcessorFormatter handlers below
    # are the ONLY sinks after configure_logging returns.
    root = logging.getLogger()
    for h in list(root.handlers):
        with contextlib.suppress(Exception):
            h.close()
        root.removeHandler(h)

    root.setLevel(getattr(logging, config.level))
    root.addHandler(_build_stderr_handler())  # D-17 stdout sink (stderr in fact)
    root.addHandler(_build_file_handler(log_dir))  # D-17 audit-trail file sink

    _CONFIGURED = True


def bind_run_id(run_id: str | UUID) -> None:
    """Bind ``run_id`` to structlog contextvars (D-19).

    Every subsequent log event in the same thread/task carries
    ``run_id=<value>``. Call this once per CLI invocation, immediately after
    :func:`configure_logging`.

    Accepts either a ``str`` or a :class:`uuid.UUID`; the value is coerced
    to ``str`` before binding so JSON serialization is unambiguous.
    """
    bind_contextvars(run_id=str(run_id))


def clear_run_id() -> None:
    """Clear all bound contextvars.

    Primarily for test isolation — production CLIs do not call this. Wraps
    :func:`structlog.contextvars.clear_contextvars` so callers do not need
    to import structlog directly.
    """
    clear_contextvars()


__all__ = [
    "DEFAULT_REDACT_KEYS",
    "REDACTED_PLACEHOLDER",
    "REDACT_PATTERNS",
    "bind_run_id",
    "clear_run_id",
    "configure_logging",
    "redaction_processor",
]
