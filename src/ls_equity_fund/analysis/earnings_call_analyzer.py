"""ANAL-11 — Earnings-call analyzer (v2 stub).

Per PROJECT.md and v1 scope: transcript pipeline (TRANSCRIPT-01) is deferred
to v2. This module ships the stub interface so the orchestrator can call it
uniformly with the other 4 analyzers; it always returns ``None``.

When v2 lands, this module is replaced — the call-site signature stays
identical so no other code changes.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

import structlog

from ls_equity_fund.analysis.claude_client import ClaudeClient

log = structlog.get_logger(__name__)

ANALYZER_TYPE = "earnings_call"


def analyze(
    *,
    conn: sqlite3.Connection,
    client: ClaudeClient,
    ticker: str,
    asof: date,
    run_id: str | None = None,
    use_cache: bool = True,
    ttl_days: int = 30,
) -> dict[str, Any] | None:
    """Stub returning None per ANAL-11 spec ("v2 deferred").

    Signature mirrors the other analyzers so the orchestrator's per-analyzer
    dispatch needs no special-casing for the missing transcript pipeline.
    """
    log.debug(
        "earnings_call_stub",
        ticker=ticker,
        asof=asof.isoformat(),
        note="v2 deferred (TRANSCRIPT-01)",
    )
    return None


def estimate_run_cost(n_tickers: int) -> float:
    """Stub costs nothing — no Claude call."""
    return 0.0


__all__ = ["ANALYZER_TYPE", "analyze", "estimate_run_cost"]
