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


def test_unfilled_methods_raise_with_plan_reference() -> None:
    """Fundamentals/short/estimates filled by later plans — clear messaging."""
    provider = YFinanceProvider(session=object())
    with pytest.raises(NotImplementedError, match="01-05"):
        provider.get_fundamentals("AAPL")
    with pytest.raises(NotImplementedError, match="01-07"):
        provider.get_short_interest("AAPL", date(2026, 1, 1))
    with pytest.raises(NotImplementedError, match="01-07"):
        provider.get_estimates("AAPL", date(2026, 1, 1))
    with pytest.raises(NotImplementedError, match="01-07"):
        provider.get_next_earnings_dates("AAPL")
