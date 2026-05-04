"""L4 - Portfolio Construction layer (Phase 5+).

Public façade: build_target_portfolio(date), generate_rebalance(date)
Phase 0 ships only the Optimizer seam (portfolio/base.py).
"""
from ls_equity_fund.portfolio.base import Optimizer

__all__ = ["Optimizer"]
