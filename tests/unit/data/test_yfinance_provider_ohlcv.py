"""YFinanceProvider OHLCV unit tests — uses session injection to avoid network.

Binds Plan 01-04 Task 1: YFinanceProvider implements OHLCVProvider with
curl_cffi transport (CLAUDE.md mandate) + tenacity retries (CLAUDE.md mandate).
Other inherited ABC methods (Fundamentals/ShortInterest/Estimates) are stubbed
with NotImplementedError messages pointing at Plans 01-05 / 01-07.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.data.providers.base import OHLCVProvider
from ls_equity_fund.data.providers.yfinance_provider import (
    YFinanceError,
    YFinanceProvider,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_alembic_cfg(db: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


def test_yfinance_provider_implements_ohlcv_abc() -> None:
    p = YFinanceProvider(session=object())
    assert isinstance(p, OHLCVProvider)


def test_get_prices_normalizes_multiindex() -> None:
    """Mock yf.download to return canonical multi-ticker shape; assert MultiIndex."""
    fake = pd.DataFrame(
        {
            ("AAPL", "Open"): [100.0, 101.0],
            ("AAPL", "High"): [102.0, 103.0],
            ("AAPL", "Low"): [99.0, 100.5],
            ("AAPL", "Close"): [101.5, 102.5],
            ("AAPL", "Adj Close"): [101.5, 102.5],
            ("AAPL", "Volume"): [1_000_000, 1_100_000],
        },
        index=pd.DatetimeIndex(["2026-04-01", "2026-04-02"]),
    )
    fake.columns = pd.MultiIndex.from_tuples(fake.columns)

    with patch(
        "ls_equity_fund.data.providers.yfinance_provider.yf.download",
        return_value=fake,
    ):
        provider = YFinanceProvider(session=object())
        out = provider.get_prices(["AAPL"], date(2026, 4, 1), date(2026, 4, 2))

    assert isinstance(out.index, pd.MultiIndex)
    assert list(out.index.names) == ["ticker", "date"]
    assert "open" in out.columns
    assert "adj_close" in out.columns
    assert len(out) == 2


def test_get_prices_retries_then_raises_yfinance_error() -> None:
    """Tenacity retries 3x; final failure surfaces as YFinanceError."""
    with patch(
        "ls_equity_fund.data.providers.yfinance_provider.yf.download",
        side_effect=ConnectionError("rate-limit"),
    ) as mock_dl:
        provider = YFinanceProvider(session=object())
        with pytest.raises(YFinanceError):
            provider.get_prices(["AAPL"], date(2026, 1, 1), date(2026, 1, 2))
        # 3 attempts (stop_after_attempt(3))
        assert mock_dl.call_count == 3


def test_get_last_stored_date_reads_max(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db)
    alembic_command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db))
    conn.executemany(
        "INSERT INTO daily_prices (ticker, date, close) VALUES (?, ?, ?)",
        [("AAPL", "2026-01-01", 100.0), ("AAPL", "2026-01-15", 105.0)],
    )
    conn.commit()
    conn.close()

    provider = YFinanceProvider(db_path=db, session=object())
    assert provider.get_last_stored_date("AAPL") == date(2026, 1, 15)
    assert provider.get_last_stored_date("MSFT") is None  # absent


def test_provider_delegates_to_impl_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fundamentals/short/estimates/earnings methods must delegate to the
    ``yfinance_provider_fundamentals`` and ``yfinance_provider_secondary``
    modules — they used to be NotImplementedError stubs and were never wired,
    silently producing zero rows in production. Pin the wiring with mocks.
    """
    import pandas as pd

    fund_calls: list[tuple[Any, str]] = []
    short_calls: list[tuple[Any, str, date]] = []
    est_calls: list[tuple[Any, str, date]] = []
    earn_calls: list[tuple[Any, str, int]] = []

    def fake_fund(session: Any, ticker: str) -> pd.DataFrame:
        fund_calls.append((session, ticker))
        return pd.DataFrame()

    def fake_short(session: Any, ticker: str, asof: date) -> dict[str, Any] | None:
        short_calls.append((session, ticker, asof))
        return {"shares_short": 1}

    def fake_est(session: Any, ticker: str, asof: date) -> dict[str, Any] | None:
        est_calls.append((session, ticker, asof))
        return None

    def fake_earn(session: Any, ticker: str, lookahead: int) -> list[dict[str, Any]]:
        earn_calls.append((session, ticker, lookahead))
        return []

    monkeypatch.setattr(
        "ls_equity_fund.data.providers.yfinance_provider_fundamentals.get_fundamentals_impl",
        fake_fund,
    )
    monkeypatch.setattr(
        "ls_equity_fund.data.providers.yfinance_provider_secondary.get_short_interest_impl",
        fake_short,
    )
    monkeypatch.setattr(
        "ls_equity_fund.data.providers.yfinance_provider_secondary.get_estimates_impl",
        fake_est,
    )
    monkeypatch.setattr(
        "ls_equity_fund.data.providers.yfinance_provider_secondary.get_next_earnings_dates_impl",
        fake_earn,
    )

    sentinel_session = object()
    provider = YFinanceProvider(session=sentinel_session)
    asof = date(2026, 1, 1)

    provider.get_fundamentals("AAPL")
    provider.get_short_interest("AAPL", asof)
    provider.get_estimates("AAPL", asof)
    provider.get_next_earnings_dates("AAPL", lookahead_days=14)

    assert fund_calls == [(sentinel_session, "AAPL")]
    assert short_calls == [(sentinel_session, "AAPL", asof)]
    assert est_calls == [(sentinel_session, "AAPL", asof)]
    assert earn_calls == [(sentinel_session, "AAPL", 14)]
