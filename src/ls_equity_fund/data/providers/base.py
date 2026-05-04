"""Phase 1 sibling provider ABCs (DATA-14).

Each ABC declares one feed-type interface. Concrete providers implement
only what they can — yfinance covers OHLCV / Fundamentals / ShortInterest /
Estimates, edgartools covers Filings, the Federal Reserve scraper covers
Macro, and Polygon (DATA-14 stub) instantiates against the union of the four
yfinance-equivalent feeds plus filings + macro to validate the swap-in seam.

Per ARCHITECTURE.md §5: DataFrames cross the seam with documented index
conventions (MultiIndex layouts noted on each method).

Per CONTEXT D-22: provider implementations live as siblings under
``data/providers/`` (yfinance_provider.py, edgar_provider.py,
fred_provider.py, polygon_provider.py). The Phase 0 monolithic
``MarketDataProvider`` ABC at ``src/ls_equity_fund/data/base.py`` is RETAINED
for backward compatibility with INFRA-03; these six siblings are additive.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


class OHLCVProvider(ABC):
    """Daily OHLCV bars (DATA-03).

    Concrete: YFinanceProvider (Phase 1, Plan 01-03), PolygonProvider
    (DATA-14 stub).
    """

    @abstractmethod
    def get_prices(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        """Return OHLCV panel.

        Index: MultiIndex(['ticker', 'date']), sorted.
        Columns: open, high, low, close, adj_close, volume.
        """

    @abstractmethod
    def get_last_stored_date(self, ticker: str) -> date | None:
        """Return MAX(date) for ticker in daily_prices, or None if absent.

        Used by incremental refresh — caller fetches from
        ``last_stored_date + 1`` to today instead of a full backfill.
        """


class FundamentalsProvider(ABC):
    """Quarterly + annual income / balance sheet / cash flow statements (DATA-04)."""

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> pd.DataFrame:
        """Return all available periods for ticker.

        Index: MultiIndex(['period_end', 'period_type']) where period_type is
            in {'annual', 'quarterly'}.
        Columns: standardized fields per the migration 0002 fundamentals
            table column list (revenue, gross_profit, ..., shares_outstanding).
        """


class ShortInterestProvider(ABC):
    """Daily short-interest snapshot (DATA-08)."""

    @abstractmethod
    def get_short_interest(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        """Return ``{shares_short, short_ratio, short_percent_of_float}`` or None."""


class EstimatesProvider(ABC):
    """Daily analyst-estimates + earnings-calendar snapshot (DATA-09, DATA-10)."""

    @abstractmethod
    def get_estimates(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        """Return ``{eps_fy1, eps_fy2, rev_fy1, rev_fy2, target_price, n_analysts}`` or None."""

    @abstractmethod
    def get_next_earnings_dates(
        self, ticker: str, lookahead_days: int = 30
    ) -> list[dict[str, Any]]:
        """Return list of ``{expected_date, time_of_day, fiscal_period}`` dicts.

        Empty list if no upcoming earnings within ``lookahead_days``.
        """


class FilingsProvider(ABC):
    """SEC EDGAR filings (DATA-05, DATA-06, DATA-07)."""

    @abstractmethod
    def fetch_filings(
        self,
        ticker: str,
        forms: list[str],
        since: date | None = None,
        cache_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch filings of the given form types since ``since``.

        Returns list of ``{accession_number, ticker, cik, form_type,
        filed_date, period_of_report, filepath, content_hash}`` dicts.
        Bodies are written to ``cache_dir`` and ``filepath`` points at them
        (DATA-05 — bodies on disk, metadata in SQLite).
        """

    @abstractmethod
    def parse_form4(
        self, accession_number: str, raw_xml_path: Path
    ) -> list[dict[str, Any]]:
        """Parse Form 4 XML into per-line transactions.

        Returns list of ``{accession_number, line_no, ticker, insider_name,
        insider_title, is_officer, is_director, is_ten_percent_owner,
        transaction_code, transaction_type, shares, price_per_share,
        total_value, transaction_date, filed_date, ownership_type}`` dicts.

        ``transaction_code`` MUST be one of ``P / S / A / M / F / G / D``
        (binds CP3 — schema CHECK at the DB layer rejects anything else).
        """

    @abstractmethod
    def parse_13f(
        self, accession_number: str, raw_path: Path
    ) -> list[dict[str, Any]]:
        """Parse 13F INFORMATION TABLE into per-position rows.

        Returns list of ``{cik, fund_name, ticker, period_end, filed_date,
        shares, value_usd}`` dicts. ``period_end`` and ``filed_date`` are
        kept as distinct columns (D4) so the 45-day filing lag survives the
        seam.
        """


class MacroProvider(ABC):
    """FOMC + macro event calendar (DATA-11)."""

    @abstractmethod
    def fetch_macro_events(
        self, lookahead_days: int = 365
    ) -> list[dict[str, Any]]:
        """Return list of ``{event_id, event_type, event_date_et,
        event_date_local, description, source}`` dicts within
        ``lookahead_days``.

        Implementations should raise a network error (caller's choice of
        exception type) when upstream is unreachable so the daily-refresh
        fallback can surface cached rows from ``macro_calendar``.
        """


__all__ = [
    "EstimatesProvider",
    "FilingsProvider",
    "FundamentalsProvider",
    "MacroProvider",
    "OHLCVProvider",
    "ShortInterestProvider",
]
