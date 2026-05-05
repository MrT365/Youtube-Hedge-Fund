"""Data provider seams (Phase 1, DATA-14).

Sibling ABCs — each concrete provider implements only the surfaces it can.
Phase 1's YFinanceProvider implements OHLCV / Fundamentals / ShortInterest /
Estimates; EdgarProvider implements Filings; FedScraperProvider implements
Macro; PolygonProvider is a stub validating the swap-in seam (DATA-14).

The Phase 0 monolithic ``MarketDataProvider`` ABC at
``src/ls_equity_fund/data/base.py`` is RETAINED for backward compatibility
with INFRA-03; these siblings are additive.
"""

from __future__ import annotations

from ls_equity_fund.data.providers.base import (
    EstimatesProvider,
    FilingsProvider,
    FundamentalsProvider,
    MacroProvider,
    OHLCVProvider,
    ShortInterestProvider,
)
from ls_equity_fund.data.providers.polygon_provider import PolygonProvider
from ls_equity_fund.data.providers.yfinance_provider import (
    YFinanceError,
    YFinanceProvider,
)

__all__ = [
    "EstimatesProvider",
    "FilingsProvider",
    "FundamentalsProvider",
    "MacroProvider",
    "OHLCVProvider",
    "PolygonProvider",
    "ShortInterestProvider",
    "YFinanceError",
    "YFinanceProvider",
]
