"""Daily short-interest snapshot (DATA-08).

Fans out across the active universe (``universe.delisted_date IS NULL``),
calls ``provider.get_short_interest(ticker, today)`` per name, and persists
one row per ticker per day in ``short_interest`` (PK ``(ticker,
snapshot_date)``). Re-running the same day is idempotent (INSERT OR IGNORE).

Per-ticker failures are log+continue: the orchestrator records a
``refresh_state`` row with ``status='FAILED'`` + truncated error and moves
on to the next ticker. Same pattern as Plan 01-04 prices ingest.

The 30/60/90-day estimate-revisions factor (Phase 2) reconstructs revisions
from the historical snapshot rows here — that is why this module is daily,
append-only, with snapshot_date in the PK.
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


def refresh_short_interest(
    config: Config,
    conn: sqlite3.Connection | None = None,
    *,
    tickers: list[str] | None = None,
    today: date | None = None,
    provider: Any = None,
) -> dict[str, int]:
    """Refresh today's short-interest snapshot across the active universe.

    Args:
        config: project Config (data.cache_dir, data.yfinance_max_workers).
        conn: optional caller-owned connection. When None, opens its own
            against ``get_db_path(config)`` and closes it on exit.
        tickers: optional explicit ticker list (default: active universe).
        today: optional date override (default: ``date.today()``); used by
            tests + freezegun-driven backfills.
        provider: optional provider override (default: ``YFinanceProvider``);
            tests pass a MagicMock-shaped fake here.

    Returns:
        ``{"ok": int, "failed": int, "rows_written": int}``.
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

        ok = failed = rows_written = 0
        with ThreadPoolExecutor(max_workers=config.data.yfinance_max_workers) as pool:
            futures = {pool.submit(provider.get_short_interest, t, today): t for t in tickers}
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    snapshot = fut.result()
                    if snapshot is None:
                        _persist_state(conn, ticker, today_str, "SKIPPED", "no data")
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO short_interest
                           (ticker, snapshot_date, shares_short,
                            short_ratio, short_percent_of_float)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            ticker,
                            today_str,
                            snapshot.get("shares_short"),
                            snapshot.get("short_ratio"),
                            snapshot.get("short_percent_of_float"),
                        ),
                    )
                    rows_written += 1
                    _persist_state(conn, ticker, today_str, "OK", None)
                    ok += 1
                except YFinanceError as e:
                    log.error("short_interest_failed", ticker=ticker, error=str(e))
                    _persist_state(conn, ticker, None, "FAILED", str(e)[:500])
                    failed += 1
                except Exception as e:
                    log.error(
                        "short_interest_unexpected",
                        ticker=ticker,
                        error=str(e),
                    )
                    _persist_state(conn, ticker, None, "FAILED", str(e)[:500])
                    failed += 1

        result = {"ok": ok, "failed": failed, "rows_written": rows_written}
        log.info("refresh_short_interest_complete", **result)
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
    """Upsert a refresh_state row for (yfinance, short_interest, ticker)."""
    conn.execute(
        """INSERT OR REPLACE INTO refresh_state
           (provider, feed_type, ticker, last_value_text, last_value_int,
            last_refreshed, status, last_error)
           VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
        (
            "yfinance",
            "short_interest",
            ticker,
            last_text,
            int(time.time()),
            status,
            last_error,
        ),
    )


__all__ = ["refresh_short_interest"]
