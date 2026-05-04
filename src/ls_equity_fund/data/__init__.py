"""L1 - Data Infrastructure layer (Phase 1+).

Public façade: refresh_all(), refresh_prices(), get_fundamentals(), ...
Phase 0 ships only the MarketDataProvider seam (data/base.py).
"""
from ls_equity_fund.data.base import MarketDataProvider

__all__ = ["MarketDataProvider"]
