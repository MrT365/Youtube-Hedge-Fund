"""Filings refresh orchestrator (DATA-05, DATA-06).

Iterates the active universe × forms list, fetches via ``EdgarProvider`` (which
uses ``edgartools`` for the EDGAR HTTP path with built-in 10 req/s rate-limit +
User-Agent compliance), persists raw bodies on disk, and writes one row per
filing into ``filings_metadata``.

For ``form_type='4'``, also parses the XML (lxml-backed; see
``edgar_provider.py`` for the deliberate edgartools-fetches/lxml-parses split)
and writes one row per ``<nonDerivativeTransaction>`` into
``insider_transactions``.

Idempotency: ``INSERT OR IGNORE`` on both tables — re-runs against the same
accession are no-ops. Per-(provider, feed_type, ticker) cursor lives in
``refresh_state`` so subsequent runs only ask EDGAR for filings filed since
the prior ``last_value_text``.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from ls_equity_fund.config import Config, Secrets
from ls_equity_fund.data.providers.edgar_provider import EdgarProvider
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)

DEFAULT_FORMS: list[str] = ["10-K", "10-Q", "8-K", "4"]
FORM4_LOOKBACK_DAYS: int = 90  # DATA-06 — cluster-buy lookback window
NON_FORM4_LOOKBACK_DAYS: int = 5 * 365  # 10-K / 10-Q / 8-K backfill window


def refresh_filings(
    config: Config,
    secrets: Secrets,
    conn: sqlite3.Connection | None = None,
    *,
    forms: list[str] | None = None,
    tickers: list[str] | None = None,
    today: date | None = None,
    provider: Any = None,
) -> dict[str, int]:
    """Refresh SEC filings for the active universe.

    Returns counters: ok, failed, filings_inserted, insider_inserted.
    """
    today = today or date.today()
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        forms = forms or list(DEFAULT_FORMS)
        if tickers is None:
            tickers = [
                r[0]
                for r in conn.execute(
                    "SELECT ticker FROM universe WHERE delisted_date IS NULL ORDER BY ticker"
                )
            ]
        if provider is None:
            provider = EdgarProvider(sec_user_agent=secrets.sec_user_agent)

        cache_dir = Path(config.data.cache_dir) / "filings"
        cache_dir.mkdir(parents=True, exist_ok=True)

        ok = failed = 0
        filings_inserted = insider_inserted = 0

        for ticker in tickers:
            for form in forms:
                since = _last_filed_date(conn, ticker, form, today)
                try:
                    rows = provider.fetch_filings(
                        ticker,
                        [form],
                        since=since,
                        cache_dir=cache_dir,
                    )
                    for fr in rows:
                        _insert_filing(conn, fr)
                        filings_inserted += 1
                        if form == "4":
                            insider_rows = provider.parse_form4(
                                fr["accession_number"],
                                Path(fr["filepath"]),
                            )
                            for ir in insider_rows:
                                ir["filed_date"] = fr["filed_date"]
                                _insert_insider(conn, ir)
                                insider_inserted += 1
                    last_filed = max((r["filed_date"] for r in rows), default=None)
                    _persist_refresh_state(
                        conn,
                        "edgar",
                        f"filings_{form}",
                        ticker,
                        last_filed,
                        "OK",
                        None,
                    )
                    ok += 1
                except Exception as e:
                    log.error(
                        "filings_fetch_failed",
                        ticker=ticker,
                        form=form,
                        error=str(e),
                    )
                    _persist_refresh_state(
                        conn,
                        "edgar",
                        f"filings_{form}",
                        ticker,
                        None,
                        "FAILED",
                        str(e)[:500],
                    )
                    failed += 1

        result = {
            "ok": ok,
            "failed": failed,
            "filings_inserted": filings_inserted,
            "insider_inserted": insider_inserted,
        }
        log.info("refresh_filings_complete", **result)
        return result
    finally:
        if owns_conn:
            conn.close()


def _last_filed_date(
    conn: sqlite3.Connection,
    ticker: str,
    form: str,
    today: date,
) -> date | None:
    """Return the latest filed_date for (ticker, form), or a backfill anchor."""
    row = conn.execute(
        "SELECT MAX(filed_date) FROM filings_metadata WHERE ticker=? AND form_type=?",
        (ticker, form),
    ).fetchone()
    if row is None or row[0] is None:
        # First run — Form 4 uses 90-day lookback, others use 5-year window
        if form == "4":
            return today - timedelta(days=FORM4_LOOKBACK_DAYS)
        return today - timedelta(days=NON_FORM4_LOOKBACK_DAYS)
    try:
        return datetime.fromisoformat(str(row[0])).date()
    except ValueError:
        # Defensive — odd date strings shouldn't crash the run
        return today - timedelta(days=FORM4_LOOKBACK_DAYS)


def _insert_filing(conn: sqlite3.Connection, fr: dict[str, Any]) -> None:
    """Idempotent INSERT into filings_metadata (PK = accession_number)."""
    conn.execute(
        """INSERT OR IGNORE INTO filings_metadata
           (accession_number, ticker, cik, form_type, filed_date,
            period_of_report, filepath, content_hash, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fr["accession_number"],
            fr["ticker"],
            fr["cik"],
            fr["form_type"],
            fr["filed_date"],
            fr.get("period_of_report"),
            fr["filepath"],
            fr.get("content_hash"),
            int(time.time()),
        ),
    )


def _insert_insider(conn: sqlite3.Connection, ir: dict[str, Any]) -> None:
    """Idempotent INSERT into insider_transactions (PK = accession + line_no)."""
    conn.execute(
        """INSERT OR IGNORE INTO insider_transactions
           (accession_number, line_no, ticker, insider_name, insider_title,
            is_officer, is_director, is_ten_percent_owner,
            transaction_code, transaction_type,
            shares, price_per_share, total_value,
            transaction_date, filed_date, ownership_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ir["accession_number"],
            ir["line_no"],
            ir["ticker"],
            ir.get("insider_name"),
            ir.get("insider_title"),
            ir.get("is_officer", 0),
            ir.get("is_director", 0),
            ir.get("is_ten_percent_owner", 0),
            ir["transaction_code"],
            ir.get("transaction_type"),
            ir.get("shares"),
            ir.get("price_per_share"),
            ir.get("total_value"),
            ir["transaction_date"],
            ir.get("filed_date") or "",
            ir.get("ownership_type"),
        ),
    )


def _persist_refresh_state(
    conn: sqlite3.Connection,
    provider_name: str,
    feed_type: str,
    ticker: str,
    last_value_text: str | None,
    status: str,
    last_error: str | None,
) -> None:
    """Upsert per-(provider, feed_type, ticker) cursor row."""
    conn.execute(
        """INSERT OR REPLACE INTO refresh_state
           (provider, feed_type, ticker, last_value_text, last_value_int,
            last_refreshed, status, last_error)
           VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
        (
            provider_name,
            feed_type,
            ticker,
            last_value_text,
            int(time.time()),
            status,
            last_error,
        ),
    )


__all__ = [
    "DEFAULT_FORMS",
    "FORM4_LOOKBACK_DAYS",
    "NON_FORM4_LOOKBACK_DAYS",
    "refresh_filings",
]
