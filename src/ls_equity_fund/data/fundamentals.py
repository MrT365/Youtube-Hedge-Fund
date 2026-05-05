"""Fundamentals refresh orchestrator (DATA-04).

Append-only via PK ``(ticker, period_end, period_type, as_of_ingest_date)`` —
mitigates D2 (yfinance restated fundamentals look-ahead, PITFALLS.md
CRITICAL). Today's INSERT OR IGNORE is a no-op if today's
``as_of_ingest_date`` row already exists; tomorrow's run writes new rows even
if yfinance has restated periods. The original row is preserved so PIT-aware
backtests (v2) can read ``WHERE as_of_ingest_date <= replay_date``.

NEVER use UPDATE / INSERT OR REPLACE against ``fundamentals``. The append-only
discipline IS the v1 D2 mitigation; if you find yourself reaching for UPSERT,
stop and re-read PITFALLS.md D2.
"""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import structlog

from ls_equity_fund.config import Config
from ls_equity_fund.data.providers.yfinance_provider import (
    YFinanceError,
    YFinanceProvider,
)
from ls_equity_fund.data.providers.yfinance_provider_fundamentals import SCHEMA_COLS
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)


def refresh_fundamentals(
    config: Config,
    conn: sqlite3.Connection | None = None,
    *,
    tickers: list[str] | None = None,
    today: date | None = None,
    provider: Any = None,
) -> dict[str, int]:
    """Refresh fundamentals for active universe tickers (D2-safe append-only).

    Args:
        config: runtime Config (reads cache_dir + max_workers).
        conn: optional preopened connection (tests). Default opens its own.
        tickers: optional explicit ticker list. Default = active universe
            (delisted_date IS NULL). Benchmarks excluded — ETFs do not have
            meaningful per-statement fundamentals.
        today: ingestion date stamp (test override). Default ``date.today()``.
        provider: optional injected provider (test override). Default
            constructs a real ``YFinanceProvider``.

    Returns:
        ``{"ok": N, "failed": M, "rows_written": R}``.
    """
    today = today or date.today()
    today_str = today.isoformat()
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        if tickers is None:
            tickers = [
                r[0]
                for r in conn.execute(
                    "SELECT ticker FROM universe WHERE delisted_date IS NULL ORDER BY ticker"
                )
            ]
        if provider is None:
            provider = YFinanceProvider(db_path=get_db_path(config))

        # Plan 04 adds DataConfig.yfinance_max_workers; Plan 05 ships in
        # parallel and must not break if Plan 04 hasn't merged yet (Rule 3
        # auto-fix — see SUMMARY.md Deviations).
        max_workers = getattr(config.data, "yfinance_max_workers", 8)

        ok = failed = 0
        rows_written = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(provider.get_fundamentals, t): t for t in tickers}
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    df = fut.result()
                    n = _persist_fundamentals(conn, ticker, df, today_str)
                    rows_written += n
                    _persist_refresh_state(conn, ticker, today_str, "OK", None)
                    ok += 1
                except YFinanceError as e:
                    log.error("fundamentals_fetch_failed", ticker=ticker, error=str(e))
                    _persist_refresh_state(conn, ticker, None, "FAILED", str(e)[:500])
                    failed += 1
                except Exception as e:
                    log.error("fundamentals_unexpected_error", ticker=ticker, error=str(e))
                    _persist_refresh_state(conn, ticker, None, "FAILED", str(e)[:500])
                    failed += 1

        result = {"ok": ok, "failed": failed, "rows_written": rows_written}
        log.info("refresh_fundamentals_complete", **result)
        return result
    finally:
        if owns_conn:
            conn.close()


def _persist_fundamentals(conn: sqlite3.Connection, ticker: str, df: Any, today_str: str) -> int:
    """APPEND-ONLY insert per (ticker, period_end, period_type, today).

    INSERT OR IGNORE means same-day reruns are no-ops (PK collision); future
    days with restated values write new rows (different as_of_ingest_date).
    NEVER swap to INSERT OR REPLACE — that would silently destroy D2
    mitigation by overwriting historical-as-of rows.
    """
    if df is None or getattr(df, "empty", False):
        return 0
    n = 0
    placeholders = ", ".join(["?"] * (4 + len(SCHEMA_COLS)))
    cols = "ticker, period_end, period_type, as_of_ingest_date, " + ", ".join(SCHEMA_COLS)
    sql = f"INSERT OR IGNORE INTO fundamentals ({cols}) VALUES ({placeholders})"
    for (period_end, period_type), row in df.iterrows():
        values: list[Any] = [ticker, period_end, period_type, today_str]
        for col in SCHEMA_COLS:
            v = row.get(col) if hasattr(row, "get") else row[col]
            # NaN-safe coercion
            if v is None:
                values.append(None)
            else:
                try:
                    if v != v:  # NaN check
                        values.append(None)
                    else:
                        values.append(float(v))
                except (TypeError, ValueError):
                    values.append(None)
        conn.execute(sql, values)
        n += 1
    return n


def _persist_refresh_state(
    conn: sqlite3.Connection,
    ticker: str,
    last_value_text: str | None,
    status: str,
    last_error: str | None,
) -> None:
    """Per-(provider, feed_type, ticker) cursor row (DATA-12)."""
    conn.execute(
        """INSERT OR REPLACE INTO refresh_state
           (provider, feed_type, ticker, last_value_text, last_value_int,
            last_refreshed, status, last_error)
           VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
        ("yfinance", "fundamentals", ticker, last_value_text, int(time.time()), status, last_error),
    )


__all__ = ["refresh_fundamentals"]
