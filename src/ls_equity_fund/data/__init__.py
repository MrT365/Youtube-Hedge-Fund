"""L1 - Data Infrastructure layer (Phase 1+).

Public façade: refresh_all(), refresh_prices(), get_fundamentals(), ...
Phase 0 ships the monolithic MarketDataProvider seam (data/base.py).
Phase 1 adds six sibling provider ABCs (data/providers/base.py) plus the
DATA-14 PolygonProvider stub. Both sets of ABCs are re-exported here so
downstream layers can import either flavor from ``ls_equity_fund.data``.
"""

from ls_equity_fund.data.base import MarketDataProvider
from ls_equity_fund.data.benchmarks import refresh_benchmarks
from ls_equity_fund.data.fundamentals import refresh_fundamentals
from ls_equity_fund.data.prices import refresh_prices
from ls_equity_fund.data.providers import (
    EstimatesProvider,
    FilingsProvider,
    FundamentalsProvider,
    MacroProvider,
    OHLCVProvider,
    PolygonProvider,
    ShortInterestProvider,
    YFinanceError,
    YFinanceProvider,
)
from ls_equity_fund.data.ratios import compute_all_ratios, compute_ratios
from ls_equity_fund.data.universe import build_universe, merge_universe_pit

__all__ = [
    "EstimatesProvider",
    "FilingsProvider",
    "FundamentalsProvider",
    "MacroProvider",
    "MarketDataProvider",
    "OHLCVProvider",
    "PolygonProvider",
    "ShortInterestProvider",
    "YFinanceError",
    "YFinanceProvider",
    "build_universe",
    "compute_all_ratios",
    "compute_ratios",
    "merge_universe_pit",
    "refresh_benchmarks",
    "refresh_fundamentals",
    "refresh_prices",
]
