"""L4 — Portfolio Construction layer.

Phase 5 ships the conviction-tilt optimiser + transaction-cost model + beta
calc + rebalance generator + schedule advisory. MVO is a Phase 7 swap-in
behind the same Optimizer ABC.

Public façade:
  * ``ConvictionTiltOptimizer`` (PORT-01)
  * ``MVOOptimizer`` (PORT-02 / PORT-03 — stub raises until Phase 7)
  * ``build_target_book``, ``load_candidate_frame``, ``compute_betas``,
    ``aggregate_book_beta``, ``estimate_trade_cost``, ``generate_rebalance``,
    ``evaluate_schedule``
"""

from ls_equity_fund.portfolio.base import Optimizer
from ls_equity_fund.portfolio.beta import (
    BookBeta,
    aggregate_book_beta,
    compute_betas,
)
from ls_equity_fund.portfolio.conviction_tilt import (
    ConvictionTiltOptimizer,
    ConvictionTiltResult,
    build_target_book,
    load_candidate_betas,
    load_candidate_frame,
    select_candidates,
)
from ls_equity_fund.portfolio.factor_exposure import compute_factor_exposure
from ls_equity_fund.portfolio.mvo import MVOOptimizer
from ls_equity_fund.portfolio.rebalance import RebalanceSummary, generate_rebalance
from ls_equity_fund.portfolio.schedule import (
    Advisory,
    ScheduleAdvisories,
    evaluate_schedule,
)
from ls_equity_fund.portfolio.state import (
    PORTFOLIO_AGGREGATE_TICKER,
    close_position,
    load_current_positions,
    upsert_position,
    write_portfolio_history,
    write_position_approvals,
)
from ls_equity_fund.portfolio.transaction_cost import TradeCost, estimate_trade_cost

__all__ = [
    "PORTFOLIO_AGGREGATE_TICKER",
    "Advisory",
    "BookBeta",
    "ConvictionTiltOptimizer",
    "ConvictionTiltResult",
    "MVOOptimizer",
    "Optimizer",
    "RebalanceSummary",
    "ScheduleAdvisories",
    "TradeCost",
    "aggregate_book_beta",
    "build_target_book",
    "close_position",
    "compute_betas",
    "compute_factor_exposure",
    "estimate_trade_cost",
    "evaluate_schedule",
    "generate_rebalance",
    "load_candidate_betas",
    "load_candidate_frame",
    "load_current_positions",
    "select_candidates",
    "upsert_position",
    "write_portfolio_history",
    "write_position_approvals",
]
