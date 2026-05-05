"""Earnings calendar refresh (DATA-10) — next-30-day window per active universe.

Unlike short_interest / estimates this writes 0..N rows per ticker (one
per upcoming earnings event within the lookahead window). Re-runs use
INSERT OR REPLACE keyed on ``(ticker, expected_date)`` so the latest
``time_of_day`` / ``fiscal_period`` overwrite stale values when yfinance
revises a date.

Each refresh first PURGES rows with ``expected_date < today`` so the table
does not grow unbounded with stale calendar entries. Phase 5's earnings-
blackout veto reads only forward-looking rows.

Per PITFALLS D6: yfinance earnings dates are noisy. We record what
yfinance reports; downstream Phase 5 applies a 5-day buffer.
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
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)


def refresh_earnings_calendar(
    config: Config,
    conn: sqlite3.Connection | None = None,
    *,
    tickers: list[str] | None = None,
    today: date | None = None,
    provider: Any = None,
    lookahead_days: int = 30,
) -> dict[str, int]:
    """Refresh upcoming earnings within ``lookahead_days`` for the active universe.

    Args:
        config: project Config (data.cache_dir, data.yfinance_max_workers).
        conn: optional caller-owned connection.
        tickers: optional explicit ticker list (default: active universe).
        today: optional date override.
        provider: optional provider override.
        lookahead_days: window for "upcoming" — anything past it is ignored
            even if yfinance reports it (default 30).

    Returns:
        ``{"ok": int, "failed": int, "rows_written": int}``. ``ok`` counts
        tickers processed (regardless of whether they had upcoming earnings),
        ``rows_written`` counts actual calendar rows persisted.
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
                    "SELECT ticker FROM universe "
                    "WHERE delisted_date IS NULL ORDER BY ticker"
                )
            ]
        if provider is None:
            provider = YFinanceProvider(db_path=get_db_path(config))

        # Purge expired earnings rows (already happened — no longer upcoming).
        conn.execute(
            "DELETE FROM earnings_calendar WHERE expected_date < ?",
            (today_str,),
        )

        ok = failed = rows_written = 0
        with ThreadPoolExecutor(
            max_workers=config.data.yfinance_max_workers
        ) as pool:
            futures = {
                pool.submit(
                    provider.get_next_earnings_dates, t, lookahead_days
                ): t
                for t in tickers
            }
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    events = fut.result()
                    for ev in events:
                        conn.execute(
                            """INSERT OR REPLACE INTO earnings_calendar
                               (ticker, expected_date, time_of_day,
                                fiscal_period, refreshed_at)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                ticker,
                                ev["expected_date"],
                                ev.get("time_of_day", ""),
                                ev.get("fiscal_period", ""),
                                int(time.time()),
                            ),
                        )
                        rows_written += 1
                    _persist_state(conn, ticker, today_str, "OK", None)
                    ok += 1
                except YFinanceError as e:
                    log.error(
                        "earnings_calendar_failed",
                        ticker=ticker,
                        error=str(e),
                    )
                    _persist_state(
                        conn, ticker, None, "FAILED", str(e)[:500]
                    )
                    failed += 1
                except Exception as e:
                    log.error(
                        "earnings_calendar_unexpected",
                        ticker=ticker,
                        error=str(e),
                    )
                    _persist_state(
                        conn, ticker, None, "FAILED", str(e)[:500]
                    )
                    failed += 1

        result = {"ok": ok, "failed": failed, "rows_written": rows_written}
        log.info("refresh_earnings_calendar_complete", **result)
        return result
    finally:
        if owns_conn:
            conn.close()


def _persist_state(
    conn: sqlite3.Connection,
    ticker: str,
    last_text: str | None,
    status: str,
    last_error: str | None,
) -> None:
    """Upsert a refresh_state row for (yfinance, earnings_calendar, ticker)."""
    conn.execute(
        """INSERT OR REPLACE INTO refresh_state
           (provider, feed_type, ticker, last_value_text, last_value_int,
            last_refreshed, status, last_error)
           VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
        (
            "yfinance",
            "earnings_calendar",
            ticker,
            last_text,
            int(time.time()),
            status,
            last_error,
        ),
    )


__all__ = ["refresh_earnings_calendar"]
