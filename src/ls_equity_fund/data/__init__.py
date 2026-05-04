"""L1 - Data Infrastructure layer (Phase 1+).

Public façade: refresh_all(), refresh_prices(), get_fundamentals(), ...
Phase 0 ships the monolithic MarketDataProvider seam (data/base.py).
Phase 1 adds six sibling provider ABCs (data/providers/base.py) plus the
DATA-14 PolygonProvider stub. Both sets of ABCs are re-exported here so
downstream layers can import either flavor from ``ls_equity_fund.data``.
"""

from ls_equity_fund.data.base import MarketDataProvider
from ls_equity_fund.data.providers import (
    EstimatesProvider,
    FilingsProvider,
    FundamentalsProvider,
    MacroProvider,
    OHLCVProvider,
    PolygonProvider,
    ShortInterestProvider,
)
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
    "build_universe",
    "merge_universe_pit",
]
