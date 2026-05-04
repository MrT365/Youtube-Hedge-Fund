"""yfinance concrete provider — Plan 01-05 minimal scaffolding.

This file ships a MINIMAL ``YFinanceProvider`` covering the FundamentalsProvider
surface needed by Plan 01-05 (DATA-04). Plan 01-04 (parallel wave-2 sibling)
will OVERWRITE/EXTEND this file with the full OHLCV implementation
(get_prices + get_last_stored_date) when its worktree merges. The two plans
share a file by design (frontmatter ``files_modified`` overlap); the merge
of the two worktrees is the source-of-truth assembly.

Until Plan 04 merges, instantiating this class to call ``get_prices`` raises
NotImplementedError — fundamentals refresh works in isolation. After Plan 04
merges, the OHLCV path lights up too.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ls_equity_fund.data.providers.base import (
    FundamentalsProvider,
    OHLCVProvider,
)


class YFinanceError(RuntimeError):
    """Raised when yfinance fetch fails after retry exhaustion (DATA-03/04)."""


class YFinanceProvider(OHLCVProvider, FundamentalsProvider):
    """yfinance-backed concrete provider.

    Plan 01-05 ships only the Fundamentals surface here. Plan 01-04 fills in
    OHLCV (get_prices, get_last_stored_date) on merge.
    """

    def __init__(
        self,
        session: Any | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.session = session
        self.db_path = Path(db_path) if db_path is not None else None

    # ---------- OHLCVProvider (Plan 01-04 owns the real impl) ----------
    def get_prices(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        raise NotImplementedError(
            "OHLCV ingest is owned by Plan 01-04; this scaffolding only covers "
            "Plan 01-05 fundamentals. Wait for Plan 04 to merge."
        )

    def get_last_stored_date(self, ticker: str) -> date | None:
        raise NotImplementedError(
            "OHLCV ingest is owned by Plan 01-04; this scaffolding only covers "
            "Plan 01-05 fundamentals. Wait for Plan 04 to merge."
        )

    # ---------- FundamentalsProvider (Plan 01-05 — DATA-04) ----------
    def get_fundamentals(self, ticker: str) -> pd.DataFrame:
        """Return MultiIndex(['period_end', 'period_type']) DataFrame.

        Wraps ``get_fundamentals_impl`` with a YFinanceError boundary so
        callers (the orchestrator) can log+continue on failure without
        leaking yfinance/tenacity specifics.
        """
        from ls_equity_fund.data.providers.yfinance_provider_fundamentals import (
            get_fundamentals_impl,
        )

        try:
            return get_fundamentals_impl(self.session, ticker)
        except Exception as e:
            raise YFinanceError(f"fundamentals fetch failed for {ticker}: {e}") from e


__all__ = ["YFinanceError", "YFinanceProvider"]
