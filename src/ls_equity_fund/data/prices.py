"""OHLCV refresh orchestrator (DATA-03).

Reads ``universe`` ∪ ``benchmarks`` for the ticker list (excluding delisted
universe rows), computes the per-ticker incremental window via
``OHLCVProvider.get_last_stored_date``, fetches via the configured provider,
persists to ``daily_prices`` with INSERT OR IGNORE, and updates
``refresh_state``.

Per Plan 01-04 plan-level decision: per-ticker failures **log+continue** —
the daily run must complete even when 1% of yfinance calls fail. A run that
aborts on the first failure never finishes against ~3000 names. Persistent
failure surfaces as ``refresh_state(provider='yfinance', feed_type='ohlcv',
ticker=T, status='FAILED', last_error=str(e)[:500])``.

Per ARCHITECTURE.md §7 / Plan-level decision: ``ThreadPoolExecutor`` with
``config.data.yfinance_max_workers`` (default 8) — yfinance is sync, threads
not asyncio. 8 chosen to balance throughput vs Yahoo bot-detection.
"""

from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, cast

import pandas as pd
import structlog

from ls_equity_fund.config import Config
from ls_equity_fund.data.providers.yfinance_provider import (
    YFinanceError,
    YFinanceProvider,
)
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)


def refresh_prices(
    config: Config,
    conn: sqlite3.Connection | None = None,
    *,
    tickers: list[str] | None = None,
    today: date | None = None,
    provider: Any = None,
) -> dict[str, int]:
    """Refresh ``daily_prices`` for ``tickers`` (default = universe ∪ benchmarks).

    Args:
        config: validated runtime Config.
        conn: optional sqlite3.Connection. If None, opens one from
            ``get_db_path(config)`` and closes it on exit.
        tickers: explicit ticker list (test override). Default fetches the
            non-delisted ``universe`` rows ∪ all ``benchmarks``.
        today: anchor date for the incremental window (test override). Defaults
            to ``date.today()``.
        provider: pre-built provider (test injection). Default is
            ``YFinanceProvider(db_path=get_db_path(config))``.

    Returns:
        ``{"ok": N, "failed": M, "skipped": K, "rows_written": R}``.
    """
    today = today or date.today()
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        if tickers is None:
            tickers = _load_default_tickers(conn)

        if provider is None:
            provider = YFinanceProvider(db_path=get_db_path(config))

        start_date = today - timedelta(days=config.data.lookback_years * 366)
        ok = failed = skipped = 0
        rows_written = 0

        # Build per-ticker work items, short-circuiting tickers already current.
        work: list[tuple[str, date, date]] = []
        for t in tickers:
            last = provider.get_last_stored_date(t)
            if last is None:
                fetch_start = start_date
            elif last >= today:
                _persist_refresh_state(conn, t, last.isoformat(), "SKIPPED", None)
                skipped += 1
                continue
            else:
                fetch_start = last + timedelta(days=1)
            work.append((t, fetch_start, today))

        # Parallel fetch with bounded concurrency
        max_workers = config.data.yfinance_max_workers
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, provider, t, s, e): t for (t, s, e) in work}
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    df = fut.result()
                    n = _persist_prices(conn, df)
                    rows_written += n
                    last_str = _df_max_date_str(df)
                    _persist_refresh_state(conn, ticker, last_str, "OK", None)
                    ok += 1
                except YFinanceError as e:
                    # Plan-level: log+continue — the daily run must complete.
                    log.error("price_fetch_failed", ticker=ticker, error=str(e))
                    _persist_refresh_state(conn, ticker, None, "FAILED", str(e)[:500])
                    failed += 1
                except Exception as e:  # pragma: no cover — defensive
                    log.error(
                        "price_fetch_unexpected_error",
                        ticker=ticker,
                        error=str(e),
                    )
                    _persist_refresh_state(conn, ticker, None, "FAILED", str(e)[:500])
                    failed += 1

        result = {
            "ok": ok,
            "failed": failed,
            "skipped": skipped,
            "rows_written": rows_written,
        }
        log.info("refresh_prices_complete", **result)
        return result
    finally:
        if owns_conn:
            conn.close()


def _load_default_tickers(conn: sqlite3.Connection) -> list[str]:
    """``universe`` (non-delisted) ∪ ``benchmarks`` — distinct, sorted."""
    rows = conn.execute(
        """SELECT ticker FROM universe WHERE delisted_date IS NULL
           UNION
           SELECT ticker FROM benchmarks
           ORDER BY 1"""
    ).fetchall()
    return [r[0] for r in rows]


def _fetch_one(provider: Any, ticker: str, start: date, end: date) -> pd.DataFrame:
    return provider.get_prices([ticker], start, end)


def _df_max_date_str(df: pd.DataFrame) -> str:
    last_date = df.index.get_level_values("date").max()
    if hasattr(last_date, "date"):
        return cast("str", last_date.date().isoformat())
    return str(last_date)[:10]


def _persist_prices(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """INSERT OR IGNORE rows into ``daily_prices``. Returns rowcount."""
    n = 0
    for (ticker, dt), row in df.iterrows():
        date_str = dt.date().isoformat() if hasattr(dt, "date") else str(dt)[:10]
        conn.execute(
            """INSERT OR IGNORE INTO daily_prices
               (ticker, date, open, high, low, close, adj_close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                date_str,
                _f(row, "open"),
                _f(row, "high"),
                _f(row, "low"),
                _f(row, "close"),
                _f(row, "adj_close"),
                _i(row, "volume"),
            ),
        )
        n += 1
    return n


def _f(row: pd.Series, col: str) -> float | None:
    if col not in row.index:
        return None
    v = row[col]
    return None if v != v else float(v)  # NaN check via NaN != NaN


def _i(row: pd.Series, col: str) -> int | None:
    if col not in row.index:
        return None
    v = row[col]
    return None if v != v else int(v)


def _persist_refresh_state(
    conn: sqlite3.Connection,
    ticker: str,
    last_value_text: str | None,
    status: str,
    last_error: str | None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO refresh_state
           (provider, feed_type, ticker, last_value_text, last_value_int,
            last_refreshed, status, last_error)
           VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
        (
            "yfinance",
            "ohlcv",
            ticker,
            last_value_text,
            int(time.time()),
            status,
            last_error,
        ),
    )


__all__ = ["refresh_prices"]
