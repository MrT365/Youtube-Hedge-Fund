"""Benchmark + sector-ETF + macro ticker registry (DATA-02).

Reads ticker lists from config.yaml — NEVER hardcoded (anti-recommendation rule).
Writes to the `benchmarks` table with category in {benchmark, sector_etf, macro}.
Plan 04's OHLCV refresh reads this table to know which non-universe tickers
to fetch prices for.
"""
from __future__ import annotations

import sqlite3
import time

import structlog

from ls_equity_fund.config import Config
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)

# Human-readable descriptions for the spec-default tickers. If an operator
# adds a custom ticker via config.yaml, description defaults to "" (the table
# is informational; downstream code keys on `category` only).
_DESCRIPTIONS: dict[str, str] = {
    "SPY": "S&P 500 ETF (benchmark)",
    "QQQ": "Nasdaq 100 ETF",
    "IWM": "Russell 2000 ETF (small cap)",
    "DIA": "Dow Jones Industrial Average ETF",
    "XLK": "Technology Select Sector SPDR",
    "XLF": "Financial Select Sector SPDR",
    "XLV": "Health Care Select Sector SPDR",
    "XLE": "Energy Select Sector SPDR",
    "XLI": "Industrial Select Sector SPDR",
    "XLC": "Communication Services Select Sector SPDR",
    "XLY": "Consumer Discretionary Select Sector SPDR",
    "XLP": "Consumer Staples Select Sector SPDR",
    "XLB": "Materials Select Sector SPDR",
    "XLRE": "Real Estate Select Sector SPDR",
    "XLU": "Utilities Select Sector SPDR",
    "^VIX": "CBOE Volatility Index",
    "TLT": "20+ Year Treasury Bond ETF",
    "HYG": "iShares iBoxx $ High Yield Corporate Bond ETF",
}


def refresh_benchmarks(
    config: Config,
    conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    """Refresh the benchmarks table from config-driven lists.

    Returns count per category: {"benchmark": N, "sector_etf": M, "macro": K}.
    Idempotent — uses INSERT OR REPLACE; safe to re-run.
    """
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        now_ts = int(time.time())
        counts = {"benchmark": 0, "sector_etf": 0, "macro": 0}

        groups: list[tuple[str, list[str]]] = [
            ("benchmark", config.data.benchmarks),
            ("sector_etf", config.data.sector_etfs),
            ("macro", config.data.macro_tickers),
        ]

        conn.execute("BEGIN")
        try:
            for category, tickers in groups:
                for t in tickers:
                    description = _DESCRIPTIONS.get(t, "")
                    conn.execute(
                        "INSERT OR REPLACE INTO benchmarks "
                        "(ticker, category, description, last_updated) "
                        "VALUES (?, ?, ?, ?)",
                        (t, category, description, now_ts),
                    )
                    counts[category] += 1
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        log.info("benchmarks_refreshed", **counts)
        return counts
    finally:
        if owns_conn:
            conn.close()


__all__ = ["refresh_benchmarks"]
