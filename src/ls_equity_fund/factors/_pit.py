"""Point-in-time helpers shared by Phase 2 factor modules.

Fundamentals and estimates lookups must be bounded by the scoring date. Ignoring
``as_of_ingest_date`` or snapshot dates creates look-ahead contamination during
historical replay.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

_FUNDAMENTALS_PIT_SQL = """
    WITH latest AS (
        SELECT ticker, period_end, period_type, MAX(as_of_ingest_date) AS aoid
        FROM fundamentals
        WHERE ticker = ? AND period_type = ?
          AND as_of_ingest_date <= ? AND period_end <= ?
        GROUP BY ticker, period_end, period_type
    )
    SELECT f.* FROM fundamentals f
    JOIN latest l USING (ticker, period_end, period_type)
    WHERE f.as_of_ingest_date = l.aoid
    ORDER BY f.period_end DESC
    LIMIT ?
"""


def latest_fundamentals_pit(
    conn: sqlite3.Connection,
    ticker: str,
    period_type: str,
    asof: date,
    n: int = 1,
) -> list[dict[str, Any]]:
    """Return latest-known fundamentals rows at or before ``asof``."""
    cur = conn.execute(
        _FUNDAMENTALS_PIT_SQL,
        (ticker, period_type, asof.isoformat(), asof.isoformat(), n),
    )
    rows = cur.fetchall()
    if not rows:
        return []
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in rows]


def latest_estimates_pit(
    conn: sqlite3.Connection,
    ticker: str,
    asof: date,
) -> dict[str, Any] | None:
    """Return latest analyst estimate snapshot at or before ``asof``."""
    cur = conn.execute(
        """
        SELECT * FROM analyst_estimates
        WHERE ticker = ? AND snapshot_date <= ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (ticker, asof.isoformat()),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row, strict=True))


def latest_close_pit(conn: sqlite3.Connection, ticker: str, asof: date) -> float | None:
    """Return latest close at or before ``asof``."""
    row = conn.execute(
        """
        SELECT close FROM daily_prices
        WHERE ticker = ? AND date <= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (ticker, asof.isoformat()),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def universe_tickers(conn: sqlite3.Connection, tickers: list[str] | None = None) -> list[str]:
    """Resolve requested tickers against the current universe."""
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker FROM universe WHERE ticker IN ({placeholders}) ORDER BY ticker",
            tickers,
        ).fetchall()
    else:
        rows = conn.execute("SELECT ticker FROM universe ORDER BY ticker").fetchall()
    return [str(row[0]) for row in rows]


__all__ = [
    "latest_close_pit",
    "latest_estimates_pit",
    "latest_fundamentals_pit",
    "universe_tickers",
]
