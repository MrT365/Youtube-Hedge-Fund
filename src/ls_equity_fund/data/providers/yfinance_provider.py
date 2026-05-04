"""yfinance concrete provider — implements the four yfinance-backed sibling ABCs.

Phase 1 ships this as the default ``data.provider``. Implements:
  - OHLCVProvider (Plan 01-04, deferred — methods raise NotImplementedError)
  - FundamentalsProvider (Plan 01-05, deferred — method raises NotImplementedError)
  - ShortInterestProvider (Plan 01-07 — IMPLEMENTED here)
  - EstimatesProvider (Plan 01-07 — IMPLEMENTED here)

Per CLAUDE.md / STACK.md: yfinance is pinned to 0.2.65 + ``curl_cffi`` session
because newer yfinance has frequently broken sub-shape; isolating the impl
behind ``yfinance_provider_secondary`` keeps API drift fixable in one place.

Per CONTEXT D-22: providers live as siblings under ``data/providers/``. The
yfinance impl class is the union of the four ABCs it can serve (OHLCV +
Fundamentals + ShortInterest + Estimates) — same pattern as
``PolygonProvider`` (which inherits from all six ABCs to validate DATA-14).

This module is hand-edited by Plans 01-04 / 01-05 / 01-07. Plan 01-07 (this
file's owner for the secondary methods) delegates to
``yfinance_provider_secondary.py`` so the retry policy + yfinance shape
coupling lives in one focused module.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ls_equity_fund.data.providers.base import (
    EstimatesProvider,
    FundamentalsProvider,
    OHLCVProvider,
    ShortInterestProvider,
)


class YFinanceError(RuntimeError):
    """Terminal yfinance fetch failure (after tenacity retries are exhausted).

    Orchestrators (refresh_short_interest / refresh_estimates /
    refresh_earnings_calendar) catch this exception per ticker and persist a
    ``refresh_state`` row with ``status='FAILED'`` + ``last_error`` text.
    """


class YFinanceProvider(
    OHLCVProvider,
    FundamentalsProvider,
    ShortInterestProvider,
    EstimatesProvider,
):
    """Concrete yfinance provider — owns OHLCV + Fundamentals + ShortInterest + Estimates.

    Args:
        db_path: SQLite path used by ``get_last_stored_date`` (Plan 01-04 wires
            this for incremental price refresh). Optional for short-interest /
            estimates / earnings flows since those snapshot today's data only.
        session: optional ``curl_cffi`` session (Plan 01-04 wires the
            project's standard yfinance session). When None, yfinance falls
            back to its internal session — acceptable for small fan-outs but
            rate-limited by Yahoo more aggressively.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        session: Any = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else None
        self.session = session

    # ------------------------------------------------------------------
    # OHLCVProvider — filled by Plan 01-04 (deferred).
    # ------------------------------------------------------------------
    def get_prices(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:  # pragma: no cover — filled by Plan 01-04
        raise NotImplementedError(
            "Filled by Plan 01-04 (DATA-03 OHLCV ingest). "
            "Plan 01-07 owns ShortInterest + Estimates only."
        )

    def get_last_stored_date(
        self, ticker: str
    ) -> date | None:  # pragma: no cover — filled by Plan 01-04
        raise NotImplementedError(
            "Filled by Plan 01-04 (DATA-03 OHLCV ingest)."
        )

    # ------------------------------------------------------------------
    # FundamentalsProvider — filled by Plan 01-05 (deferred).
    # ------------------------------------------------------------------
    def get_fundamentals(
        self, ticker: str
    ) -> pd.DataFrame:  # pragma: no cover — filled by Plan 01-05
        raise NotImplementedError(
            "Filled by Plan 01-05 (DATA-04 fundamentals ingest)."
        )

    # ------------------------------------------------------------------
    # ShortInterestProvider — Plan 01-07 (IMPLEMENTED).
    # ------------------------------------------------------------------
    def get_short_interest(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        from ls_equity_fund.data.providers.yfinance_provider_secondary import (
            get_short_interest_impl,
        )

        try:
            return get_short_interest_impl(self.session, ticker, asof)
        except Exception as e:
            raise YFinanceError(
                f"short_interest fetch failed for {ticker}: {e}"
            ) from e

    # ------------------------------------------------------------------
    # EstimatesProvider — Plan 01-07 (IMPLEMENTED).
    # ------------------------------------------------------------------
    def get_estimates(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        from ls_equity_fund.data.providers.yfinance_provider_secondary import (
            get_estimates_impl,
        )

        try:
            return get_estimates_impl(self.session, ticker, asof)
        except Exception as e:
            raise YFinanceError(
                f"estimates fetch failed for {ticker}: {e}"
            ) from e

    def get_next_earnings_dates(
        self, ticker: str, lookahead_days: int = 30
    ) -> list[dict[str, Any]]:
        from ls_equity_fund.data.providers.yfinance_provider_secondary import (
            get_next_earnings_dates_impl,
        )

        try:
            return get_next_earnings_dates_impl(
                self.session, ticker, lookahead_days
            )
        except Exception as e:
            raise YFinanceError(
                f"earnings_dates fetch failed for {ticker}: {e}"
            ) from e


__all__ = ["YFinanceError", "YFinanceProvider"]
