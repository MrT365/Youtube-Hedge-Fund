"""Polygon.io provider stub — validates DATA-14 swap-in seam.

Phase 1 ships yfinance as the default provider. This class proves the
provider seam supports drop-in replacement: instantiating ``PolygonProvider``
and selecting it via ``config.data.provider = 'polygon'`` MUST work without
rewriting downstream code. Methods raise ``NotImplementedError`` until the
v1.x Polygon integration milestone — config validation rejects
``provider == 'polygon'`` until then with a clear "DATA-14" message.

The single class inherits from all six sibling ABCs because Polygon would,
in production, supply OHLCV / Fundamentals / ShortInterest / Estimates feeds
(matching yfinance's surface) plus filings + macro substitutes. Inheriting
from the union here is what proves the seam works — adding a real
implementation is a separate v1.x plan.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ls_equity_fund.data.providers.base import (
    EstimatesProvider,
    FilingsProvider,
    FundamentalsProvider,
    MacroProvider,
    OHLCVProvider,
    ShortInterestProvider,
)

_POLYGON_DEFERRED = (
    "Polygon integration deferred — see DATA-14. "
    "Set data.provider='yfinance' in config.yaml until the Polygon milestone "
    "ships in v1.x."
)


class PolygonProvider(
    OHLCVProvider,
    FundamentalsProvider,
    ShortInterestProvider,
    EstimatesProvider,
    FilingsProvider,
    MacroProvider,
):
    """Stub implementing the union of all six provider ABCs.

    Instantiating this class succeeds (validates DATA-14 seam). Every method
    raises ``NotImplementedError`` until the Polygon integration milestone.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    # ---------- OHLCVProvider ----------
    def get_prices(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        raise NotImplementedError(_POLYGON_DEFERRED)

    def get_last_stored_date(self, ticker: str) -> date | None:
        raise NotImplementedError(_POLYGON_DEFERRED)

    # ---------- FundamentalsProvider ----------
    def get_fundamentals(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError(_POLYGON_DEFERRED)

    # ---------- ShortInterestProvider ----------
    def get_short_interest(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        raise NotImplementedError(_POLYGON_DEFERRED)

    # ---------- EstimatesProvider ----------
    def get_estimates(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        raise NotImplementedError(_POLYGON_DEFERRED)

    def get_next_earnings_dates(
        self, ticker: str, lookahead_days: int = 30
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_POLYGON_DEFERRED)

    # ---------- FilingsProvider ----------
    def fetch_filings(
        self,
        ticker: str,
        forms: list[str],
        since: date | None = None,
        cache_dir: Path | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_POLYGON_DEFERRED)

    def parse_form4(
        self, accession_number: str, raw_xml_path: Path
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_POLYGON_DEFERRED)

    def parse_13f(
        self, accession_number: str, raw_path: Path
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_POLYGON_DEFERRED)

    # ---------- MacroProvider ----------
    def fetch_macro_events(
        self, lookahead_days: int = 365
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(_POLYGON_DEFERRED)


__all__ = ["PolygonProvider"]
