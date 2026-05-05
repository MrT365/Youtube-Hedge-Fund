"""Insider-transaction analytics (DATA-06).

The Form 4 ingestion pipeline lives in :mod:`ls_equity_fund.data.filings` (it
needs the EDGAR fetch + parse pipeline). This module exposes the on-demand
analytics that read ``insider_transactions``:

  * :func:`detect_cluster_buys` — CP3-aligned: counts ONLY ``transaction_code='P'``
    (open-market purchases). A/M/F/G/D codes are NOT directional and are
    deliberately excluded.
  * :func:`flag_ceo_cfo_purchases` — CP3 + CEO/CFO weight: filters P-purchases
    by officers whose ``insider_title`` matches ``CEO_CFO_TITLE_RE``. These get
    a 3× weight in Phase 2 scoring per spec.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import structlog

from ls_equity_fund.data.providers.edgar_provider import CEO_CFO_TITLE_RE

log = structlog.get_logger(__name__)


def detect_cluster_buys(
    conn: sqlite3.Connection,
    today: date,
    *,
    window_days: int = 30,
    min_insiders: int = 3,
) -> list[dict[str, Any]]:
    """Tickers with >= ``min_insiders`` distinct P-coders in last ``window_days``.

    CP3 binding — counts ONLY ``transaction_code = 'P'`` (open-market purchases).

    Returns rows shaped::

        [{ticker, distinct_insiders, total_value, latest_date}, ...]

    sorted by ``distinct_insiders DESC`` then ``total_value DESC``.
    """
    start = (today - timedelta(days=window_days)).isoformat()
    end = today.isoformat()
    rows = conn.execute(
        """SELECT ticker,
                  COUNT(DISTINCT insider_name) AS distinct_insiders,
                  COALESCE(SUM(total_value), 0)  AS total_value,
                  MAX(transaction_date)          AS latest_date
           FROM insider_transactions
           WHERE transaction_code = 'P'
             AND transaction_date BETWEEN ? AND ?
           GROUP BY ticker
           HAVING distinct_insiders >= ?
           ORDER BY distinct_insiders DESC, total_value DESC""",
        (start, end, min_insiders),
    ).fetchall()
    return [
        {
            "ticker": r[0],
            "distinct_insiders": r[1],
            "total_value": r[2],
            "latest_date": r[3],
        }
        for r in rows
    ]


def flag_ceo_cfo_purchases(
    conn: sqlite3.Connection,
    today: date,
    *,
    window_days: int = 90,
) -> list[dict[str, Any]]:
    """P-code purchases where ``insider_title`` matches CEO/CFO regex.

    CP3 binding — only ``transaction_code = 'P'`` (open-market purchases) count.
    These rows carry the highest-confidence directional signal (3× weight in
    Phase 2 per spec).
    """
    start = (today - timedelta(days=window_days)).isoformat()
    end = today.isoformat()
    rows = conn.execute(
        """SELECT ticker, insider_name, insider_title,
                  shares, price_per_share, total_value, transaction_date
           FROM insider_transactions
           WHERE transaction_code = 'P'
             AND is_officer = 1
             AND transaction_date BETWEEN ? AND ?
           ORDER BY total_value DESC""",
        (start, end),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        title = r[2] or ""
        if CEO_CFO_TITLE_RE.search(title):
            out.append({
                "ticker": r[0],
                "insider_name": r[1],
                "insider_title": title,
                "shares": r[3],
                "price_per_share": r[4],
                "total_value": r[5],
                "transaction_date": r[6],
            })
    return out


__all__ = ["detect_cluster_buys", "flag_ceo_cfo_purchases"]
