"""MarketDataProvider seam (D-22, INFRA-03).

Phase 0 declares the abstract surface. Phase 1 ships YFinanceProvider as the
default concrete; future paid feeds (Polygon, Tiingo, IEX) plug in here without
rewriting downstream layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import pandas as pd


class MarketDataProvider(ABC):
    """Abstract market-data interface.

    Phase 1's YFinanceProvider implements every method below.
    Per ARCHITECTURE.md §5 - DataFrames cross the seam with documented index conventions.
    """

    @abstractmethod
    def get_prices(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        """Return OHLCV.

        Index: MultiIndex(ticker, date), sorted.
        Columns: open, high, low, close, adj_close, volume.
        """

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> pd.DataFrame:
        """Return fundamentals for one ticker.

        Index: period_end (DatetimeIndex).
        Columns: standardized fundamental fields (income statement + balance sheet + cash flow).
        """

    @abstractmethod
    def get_short_interest(self, ticker: str, asof: date) -> dict[str, Any] | None:
        """Return short-interest snapshot or None if no data for asof."""

    @abstractmethod
    def get_estimates(self, ticker: str, asof: date) -> dict[str, Any] | None:
        """Return analyst estimates snapshot or None if no data for asof."""


__all__ = ["MarketDataProvider"]
