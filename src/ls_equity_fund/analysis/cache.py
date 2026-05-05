"""ANAL-04 — SQLite-backed analysis result cache (30-day default TTL).

Keyed by ``(analyzer_type, ticker, artifact_id)`` — re-analyzing the same
artifact returns a free hit. The cache row also doubles as the audit record
(model, costs, run_id, computed_at) for SCORE-10-style replay.

Expiry is enforced on read: ``get()`` filters ``expires_at >= now``. A periodic
``evict_expired()`` keeps the table from growing unbounded; the orchestrator
calls it once at run end.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# 30-day default TTL per ANAL-04. Anthropic's prompt-cache (separate concern)
# is 5min-1h; this is the persistent SQLite mirror so re-runs never hit Claude
# at all for a recently analyzed artifact.
DEFAULT_TTL_DAYS = 30

ALLOWED_ANALYZER_TYPES: frozenset[str] = frozenset(
    {"earnings", "filing", "risk", "insider", "sector", "earnings_call"}
)


@dataclass(frozen=True)
class CachedResult:
    """A cache hit's payload + telemetry."""

    response: dict[str, Any]
    model: str
    computed_at: int
    expires_at: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    run_id: str | None
    cached_from: str | None


def _ensure_analyzer(analyzer_type: str) -> None:
    if analyzer_type not in ALLOWED_ANALYZER_TYPES:
        raise ValueError(
            f"unknown analyzer_type {analyzer_type!r}; expected one of "
            f"{sorted(ALLOWED_ANALYZER_TYPES)}"
        )


def get(
    conn: sqlite3.Connection,
    *,
    analyzer_type: str,
    ticker: str,
    artifact_id: str,
    now_ts: int | None = None,
) -> CachedResult | None:
    """Return a cached result iff present AND not expired.

    Expired rows are NOT auto-evicted here (read path stays read-only); call
    ``evict_expired`` periodically.
    """
    _ensure_analyzer(analyzer_type)
    now = now_ts if now_ts is not None else int(time.time())
    cur = conn.execute(
        """
        SELECT response_json, model, computed_at, expires_at,
               input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
               cost_usd, run_id, cached_from
        FROM analysis_results
        WHERE analyzer_type = ? AND ticker = ? AND artifact_id = ?
              AND expires_at >= ?
        """,
        (analyzer_type, ticker, artifact_id, now),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return CachedResult(
        response=json.loads(row[0]),
        model=row[1],
        computed_at=int(row[2]),
        expires_at=int(row[3]),
        input_tokens=int(row[4]),
        output_tokens=int(row[5]),
        cache_read_tokens=int(row[6]),
        cache_write_tokens=int(row[7]),
        cost_usd=float(row[8]),
        run_id=row[9],
        cached_from=row[10],
    )


def put(
    conn: sqlite3.Connection,
    *,
    analyzer_type: str,
    ticker: str,
    artifact_id: str,
    run_id: str | None,
    model: str,
    response: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float = 0.0,
    cached_from: str | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
    now_ts: int | None = None,
) -> None:
    """Insert or replace one cache row."""
    _ensure_analyzer(analyzer_type)
    now = now_ts if now_ts is not None else int(time.time())
    expires_at = now + ttl_days * 86_400

    response_json = json.dumps(response, sort_keys=True, default=str)

    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO analysis_results
                (analyzer_type, ticker, artifact_id, run_id, model, response_json,
                 input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                 cost_usd, cached_from, computed_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analyzer_type,
                ticker,
                artifact_id,
                run_id,
                model,
                response_json,
                int(input_tokens),
                int(output_tokens),
                int(cache_read_tokens),
                int(cache_write_tokens),
                float(cost_usd),
                cached_from,
                now,
                expires_at,
            ),
        )
    log.debug(
        "analysis_cache_put",
        analyzer_type=analyzer_type,
        ticker=ticker,
        artifact_id=artifact_id,
        ttl_days=ttl_days,
    )


def evict_expired(conn: sqlite3.Connection, *, now_ts: int | None = None) -> int:
    """Delete rows whose ``expires_at`` is in the past. Returns the number deleted."""
    now = now_ts if now_ts is not None else int(time.time())
    with conn:
        cur = conn.execute("DELETE FROM analysis_results WHERE expires_at < ?", (now,))
    n = cur.rowcount
    if n > 0:
        log.info("analysis_cache_evicted", n_rows=n)
    return n


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Snapshot counts per analyzer_type — useful for run-summary printout."""
    cur = conn.execute(
        """
        SELECT analyzer_type, COUNT(*), MIN(computed_at), MAX(computed_at)
        FROM analysis_results
        GROUP BY analyzer_type
        """
    )
    return {
        row[0]: {"rows": int(row[1]), "oldest": int(row[2]), "newest": int(row[3])}
        for row in cur.fetchall()
    }


__all__ = [
    "ALLOWED_ANALYZER_TYPES",
    "DEFAULT_TTL_DAYS",
    "CachedResult",
    "evict_expired",
    "get",
    "put",
    "stats",
]
