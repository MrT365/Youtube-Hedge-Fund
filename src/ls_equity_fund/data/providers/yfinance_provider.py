"""YFinanceProvider — yfinance-backed concrete provider (DATA-03, DATA-14 default).

Implements OHLCVProvider, FundamentalsProvider, ShortInterestProvider,
EstimatesProvider. OHLCV is filled in this plan (Plan 01-04); the other three
are stubbed and filled by Plans 05 (fundamentals) and 07 (short_int +
estimates). Filings/Macro provider responsibilities are NOT yfinance's — see
Plans 06/08.

Per CLAUDE.md "Market Data" table: yfinance pinned to 0.2.65 + ``curl_cffi``
transport for TLS impersonation; standard ``requests.Session`` is bot-detected
by Yahoo. Construct ``curl_cffi.requests.Session(impersonate="chrome")`` and
pass via ``session=`` to every ``yf.download`` / ``yf.Ticker`` call.

Per CLAUDE.md "Anthropic Claude" + "Watch List": tenacity for retry/backoff.
Failure semantics: log+continue at the orchestrator layer (this provider
raises ``YFinanceError`` after retries; ``refresh_prices`` catches it and
writes ``refresh_state.status='FAILED'``).

Per Plan 01-04 plan-level decision (curl_cffi mandatory): no
``requests-cache`` — broken with the curl_cffi transport (CLAUDE.md
anti-recommendation).
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import structlog
import yfinance as yf
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ls_equity_fund.data.providers.base import (
    EstimatesProvider,
    FundamentalsProvider,
    OHLCVProvider,
    ShortInterestProvider,
)

log = structlog.get_logger(__name__)


class YFinanceError(RuntimeError):
    """Raised after tenacity retries are exhausted; caller logs+continues."""


class YFinanceProvider(
    OHLCVProvider,
    FundamentalsProvider,
    ShortInterestProvider,
    EstimatesProvider,
):
    """Concrete yfinance provider with curl_cffi transport + tenacity retries.

    Plans 05 and 07 fill in fundamentals/short/estimates; this plan (01-04)
    fills OHLCV.
    """

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        session: Any = None,
    ) -> None:
        """Initialize.

        Args:
            db_path: SQLite path used by ``get_last_stored_date``. Required for
                incremental refresh; tests that don't exercise the DB read path
                may omit it.
            session: Optional curl_cffi session (injected for tests). Production
                call constructs ``curl_cffi.requests.Session(impersonate="chrome")``.
        """
        self.db_path = Path(db_path) if db_path else None
        if session is None:
            try:
                from curl_cffi import requests as curl_requests

                session = curl_requests.Session(impersonate="chrome")
            except ImportError:
                log.warning("curl_cffi_unavailable_using_default_session")
                session = None
        self.session = session

    # ---------- OHLCVProvider ----------

    def get_prices(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        """Fetch OHLCV for tickers between start and end (inclusive).

        Returns:
            DataFrame with MultiIndex(['ticker', 'date']), columns
            ``[open, high, low, close, adj_close, volume]``.

        Raises:
            YFinanceError: after tenacity exhausts retries (3 attempts with
                exponential backoff 1s/2s/4s).
        """
        try:
            return self._download_with_retry(tickers, start, end)
        except RetryError as e:
            raise YFinanceError(
                f"yfinance download failed for {tickers}: {e}"
            ) from e
        except Exception as e:
            # `reraise=True` on @retry causes the underlying exception to bubble
            # up directly (instead of RetryError); wrap it consistently so the
            # orchestrator only needs to catch YFinanceError.
            raise YFinanceError(
                f"yfinance download failed for {tickers}: {e}"
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _download_with_retry(
        self, tickers: list[str], start: date, end: date
    ) -> pd.DataFrame:
        # yf.download accepts list[str]; for single ticker, pass as ["AAPL"].
        # group_by="ticker" returns a hierarchical column index that we flatten
        # via _normalize_to_panel into MultiIndex(['ticker', 'date']).
        df = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yf end is exclusive
            auto_adjust=False,
            group_by="ticker",
            progress=False,
            session=self.session,
            threads=False,  # we manage our own thread pool in refresh_prices
        )
        if df is None or df.empty:
            raise ValueError(f"yfinance returned empty frame for {tickers}")
        return self._normalize_to_panel(df, tickers)

    @staticmethod
    def _normalize_to_panel(
        df: pd.DataFrame, tickers: list[str]
    ) -> pd.DataFrame:
        """Coerce yfinance output into MultiIndex(['ticker','date']) panel.

        yfinance returns either:
          - a flat column DataFrame for a single ticker, or
          - a MultiIndex columns DataFrame (level 0 = ticker, level 1 = field)
            when ``group_by="ticker"`` is used.
        We normalize both into the canonical OHLCVProvider shape.
        """
        rows: list[pd.DataFrame] = []
        if isinstance(df.columns, pd.MultiIndex):
            present_tickers = set(df.columns.get_level_values(0))
            for t in tickers:
                if t not in present_tickers:
                    continue
                sub = df[t].copy()
                sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
                sub["ticker"] = t
                sub.index.name = "date"
                rows.append(sub.reset_index().set_index(["ticker", "date"]))
        else:
            # Single-ticker shape: flat columns, no ticker level
            sub = df.copy()
            sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
            sub["ticker"] = tickers[0]
            sub.index.name = "date"
            rows.append(sub.reset_index().set_index(["ticker", "date"]))
        if not rows:
            # Should be unreachable — caller checks df.empty first — but keep a
            # defensive empty panel with the canonical schema.
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "adj_close", "volume"]
            )
        out = pd.concat(rows).sort_index()
        # yfinance emits "Adj Close" → after lower+_-replace it becomes "adj_close".
        # Defensive rename in case of "adj close" residue (older yfinance shapes).
        if "adj_close" not in out.columns and "adj close" in out.columns:
            out = out.rename(columns={"adj close": "adj_close"})
        wanted = ["open", "high", "low", "close", "adj_close", "volume"]
        return out[[c for c in wanted if c in out.columns]]

    def get_last_stored_date(self, ticker: str) -> date | None:
        """``MAX(date)`` for ticker in ``daily_prices``, or None if absent.

        Used by incremental-refresh logic in ``refresh_prices`` (Plan 01-04
        Task 2). Returns None when the ticker has never been stored, signalling
        a full lookback fetch.
        """
        if self.db_path is None:
            return None
        from ls_equity_fund.db import get_connection

        conn = get_connection(self.db_path, create_parent=False)
        try:
            row = conn.execute(
                "SELECT MAX(date) FROM daily_prices WHERE ticker=?", (ticker,)
            ).fetchone()
        finally:
            conn.close()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0]).date()

    # ---------- FundamentalsProvider (filled by Plan 01-05) ----------
    def get_fundamentals(self, ticker: str) -> pd.DataFrame:
        raise NotImplementedError(
            "Filled by Plan 01-05 (fundamentals + ratios)"
        )

    # ---------- ShortInterestProvider (filled by Plan 01-07) ----------
    def get_short_interest(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Filled by Plan 01-07 (short interest)")

    # ---------- EstimatesProvider (filled by Plan 01-07) ----------
    def get_estimates(
        self, ticker: str, asof: date
    ) -> dict[str, Any] | None:
        raise NotImplementedError("Filled by Plan 01-07 (analyst estimates)")

    def get_next_earnings_dates(
        self, ticker: str, lookahead_days: int = 30
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Filled by Plan 01-07 (earnings calendar)")


__all__ = ["YFinanceError", "YFinanceProvider"]
