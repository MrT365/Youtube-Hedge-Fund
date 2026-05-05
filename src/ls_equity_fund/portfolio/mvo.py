"""MVO optimiser stub (PORT-02 / PORT-03).

Phase 5 ships the seam only — MVO arrives in Phase 7 once L5's Ledoit-Wolf
covariance matrix is wired. The stub raises NotImplementedError on call so an
operator who flips ``optimizer: mvo`` in config before Phase 7 ships gets a
loud, actionable error instead of silently mis-trading.

The class is registered behind the same ``Optimizer`` ABC as the conviction-
tilt optimiser, validating the seam (ROADMAP Phase 0 SC3, Phase 7 SC3).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.base import Optimizer

PHASE7_DEFER_MSG = "MVO coming in Phase 7"


class MVOOptimizer(Optimizer):
    """SLSQP MVO — Phase 7 will replace the body."""

    def __init__(self, cfg: PortfolioConfig) -> None:
        self._cfg = cfg

    def optimize(
        self,
        candidates: pd.DataFrame,
        cov: Any | None,
        constraints: Any,
    ) -> pd.DataFrame:
        raise NotImplementedError(PHASE7_DEFER_MSG)


__all__ = ["PHASE7_DEFER_MSG", "MVOOptimizer"]
