"""13F institutional-holdings ingestion (DATA-07).

Iterates ``config.data.tracked_funds`` (NOT hardcoded — anti-recommendation
in CLAUDE.md), fetches each fund's 13F-HR filings via :class:`EdgarProvider`,
parses the INFORMATION TABLE, and persists one row per (fund, ticker, period).

D4 binding (PITFALLS.md) — ``period_end`` and ``filed_date`` are stored as
DISTINCT columns. The 45-day legal lag between ``period_end`` (the report
date) and ``filed_date`` (when the SEC published it) MUST be preserved so
downstream factor logic in Phase 2 can compute alpha-decay weighting based
on ``today - period_end``. Never collapse the two.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import structlog

from ls_equity_fund.config import Config, Secrets
from ls_equity_fund.data.providers.edgar_provider import EdgarProvider
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)


def refresh_institutional_holdings(
    config: Config,
    secrets: Secrets,
    conn: sqlite3.Connection | None = None,
    *,
    provider: Any = None,
) -> dict[str, int]:
    """Refresh 13F holdings for every fund in ``config.data.tracked_funds``.

    Returns counters: ok, failed, rows_written.
    """
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        if provider is None:
            provider = EdgarProvider(sec_user_agent=secrets.sec_user_agent)
        cache_dir = Path(config.data.cache_dir) / "filings"
        cache_dir.mkdir(parents=True, exist_ok=True)

        ok = failed = rows_written = 0
        for fund in config.data.tracked_funds:
            try:
                # 13F-HR is the spec form name (some funds also file 13F-HR/A).
                fund_filings = provider.fetch_filings(
                    fund.cik, ["13F-HR"], since=None, cache_dir=cache_dir,
                )
                for ff in fund_filings:
                    positions = provider.parse_13f(
                        ff["accession_number"], Path(ff["filepath"]),
                    )
                    period_end = ff.get("period_of_report") or ""
                    filed_date = ff.get("filed_date") or ""
                    for pos in positions:
                        ticker = (pos.get("ticker") or "").strip()
                        if not ticker:
                            continue
                        prior = conn.execute(
                            """SELECT shares FROM institutional_holdings
                               WHERE cik=? AND ticker=?
                                 AND period_end < ?
                               ORDER BY period_end DESC LIMIT 1""",
                            (fund.cik, ticker, period_end),
                        ).fetchone()
                        prior_shares = (
                            float(prior[0])
                            if prior is not None and prior[0] is not None
                            else 0.0
                        )
                        cur_shares = float(pos.get("shares") or 0.0)
                        change_shares = cur_shares - prior_shares
                        is_new = 1 if (prior is None and cur_shares > 0) else 0
                        conn.execute(
                            """INSERT OR REPLACE INTO institutional_holdings
                               (cik, fund_name, ticker, period_end, filed_date,
                                shares, value_usd, change_shares, is_new_position)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                fund.cik, fund.name, ticker,
                                period_end, filed_date,
                                cur_shares, pos.get("value_usd"),
                                change_shares, is_new,
                            ),
                        )
                        rows_written += 1
                ok += 1
            except Exception as e:  # noqa: BLE001 — log+continue per data layer pattern
                log.error("13f_fetch_failed", fund=fund.name, error=str(e))
                failed += 1

        result = {"ok": ok, "failed": failed, "rows_written": rows_written}
        log.info("refresh_13f_complete", **result)
        return result
    finally:
        if owns_conn:
            conn.close()


def detect_multi_fund_openings(
    conn: sqlite3.Connection,
    period_end: str,
    *,
    min_funds: int = 3,
) -> list[dict[str, Any]]:
    """Tickers where >= ``min_funds`` tracked funds opened a NEW position at ``period_end``.

    Returns rows shaped: ``[{ticker, new_funds, fund_names}, ...]`` sorted by
    ``new_funds DESC``.
    """
    rows = conn.execute(
        """SELECT ticker,
                  COUNT(DISTINCT cik)            AS new_funds,
                  GROUP_CONCAT(fund_name, ', ')  AS fund_names
           FROM institutional_holdings
           WHERE period_end = ? AND is_new_position = 1
           GROUP BY ticker
           HAVING new_funds >= ?
           ORDER BY new_funds DESC""",
        (period_end, min_funds),
    ).fetchall()
    return [
        {"ticker": r[0], "new_funds": r[1], "fund_names": r[2]}
        for r in rows
    ]


__all__ = ["detect_multi_fund_openings", "refresh_institutional_holdings"]
