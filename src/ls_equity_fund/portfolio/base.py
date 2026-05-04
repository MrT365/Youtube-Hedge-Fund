"""Optimizer seam (D-22, INFRA-03).

Phase 0 declares the abstract surface. Phase 5 ships ConvictionTiltOptimizer.
Phase 7 ships MVOOptimizer (SLSQP) - both behind the same Optimizer interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class Optimizer(ABC):
    """Abstract portfolio optimizer.

    Per ARCHITECTURE.md §11:
      - ConvictionTiltOptimizer (Phase 5) ignores `cov`.
      - MVOOptimizer (Phase 7) raises if `cov is None`.

    The cov and constraints types are deliberately loose (Any) at Phase 0.
    Phase 5 will introduce CovarianceMatrix and PortfolioConstraints types.
    """

    @abstractmethod
    def optimize(
        self,
        candidates: pd.DataFrame,
        cov: Any | None,
        constraints: Any,
    ) -> pd.DataFrame:
        """Return target weights.

        Args:
            candidates: ticker x {composite_score, sector, expected_return, ...}
            cov: optional covariance matrix (required by MVO; ignored by conviction-tilt)
            constraints: PortfolioConstraints (gross, net, per-position, sector, beta, turnover)

        Returns:
            DataFrame: index=ticker, columns=[target_weight, side, ...]
        """


__all__ = ["Optimizer"]
