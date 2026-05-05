"""MVO stub tests (PORT-02 / PORT-03 — Phase 7 swap-in)."""

from __future__ import annotations

import pandas as pd
import pytest

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.mvo import PHASE7_DEFER_MSG, MVOOptimizer


def test_mvo_raises_not_implemented() -> None:
    opt = MVOOptimizer(PortfolioConfig())
    with pytest.raises(NotImplementedError, match=PHASE7_DEFER_MSG):
        opt.optimize(pd.DataFrame(), None, {})
