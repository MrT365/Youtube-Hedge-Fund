"""L1 - Data Infrastructure layer (Phase 1+).

Public façade: refresh_filings(), refresh_institutional_holdings(),
detect_cluster_buys(), flag_ceo_cfo_purchases(), detect_multi_fund_openings(),
plus the provider ABCs.

Phase 0 ships the monolithic MarketDataProvider seam (data/base.py).
Phase 1 adds six sibling provider ABCs (data/providers/base.py) plus the
DATA-14 PolygonProvider stub. Phase 1 wave 2 (01-06) adds the EDGAR
ingestion pipeline (filings.py, insider.py, institutional.py).
"""

from ls_equity_fund.data.base import MarketDataProvider
from ls_equity_fund.data.benchmarks import refresh_benchmarks
from ls_equity_fund.data.earnings_calendar import refresh_earnings_calendar
from ls_equity_fund.data.estimates import refresh_estimates
from ls_equity_fund.data.filings import (
    DEFAULT_FORMS,
    FORM4_LOOKBACK_DAYS,
    refresh_filings,
)
from ls_equity_fund.data.fundamentals import refresh_fundamentals
from ls_equity_fund.data.insider import (
    detect_cluster_buys,
    flag_ceo_cfo_purchases,
)
from ls_equity_fund.data.institutional import (
    detect_multi_fund_openings,
    refresh_institutional_holdings,
)
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
from ls_equity_fund.data.short_interest import refresh_short_interest
from ls_equity_fund.data.universe import build_universe, merge_universe_pit

__all__ = [
    "DEFAULT_FORMS",
    "EstimatesProvider",
    "FORM4_LOOKBACK_DAYS",
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
    "detect_cluster_buys",
    "detect_multi_fund_openings",
    "flag_ceo_cfo_purchases",
    "merge_universe_pit",
    "refresh_benchmarks",
    "refresh_earnings_calendar",
    "refresh_estimates",
    "refresh_filings",
    "refresh_fundamentals",
    "refresh_institutional_holdings",
    "refresh_prices",
    "refresh_short_interest",
]
