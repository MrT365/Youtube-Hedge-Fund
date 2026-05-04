"""refresh_prices orchestrator tests — uses fake provider, no network.

Binds Plan 01-04 Task 2: orchestrator must walk universe ∪ benchmarks
(excluding delisted), compute incremental window per ticker, fetch via the
configured provider, persist to ``daily_prices`` with INSERT OR IGNORE,
update ``refresh_state``, and log+continue on per-ticker YFinanceError.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.config import load_config
from ls_equity_fund.data.prices import refresh_prices
from ls_equity_fund.data.providers.yfinance_provider import YFinanceError

REPO_ROOT = Path(__file__).resolve().parents[3]


def _alembic_cfg(db: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    return cfg


@pytest.fixture
def setup_db(tmp_path: Path) -> Path:
    db = tmp_path / "ls_equity_fund.db"
    alembic_command.upgrade(_alembic_cfg(db), "head")
    return db


@pytest.fixture
def config(setup_db: Path):
    """Load test config and point cache_dir at tmp_path."""
    config_obj, _ = load_config(yaml_path=str(REPO_ROOT / "config.yaml.example"))
    config_obj.data.cache_dir = str(setup_db.parent)
    config_obj.data.lookback_years = 1
    return config_obj


def _make_panel(ticker: str, dates: list[str]) -> pd.DataFrame:
    idx = pd.MultiIndex.from_tuples(
        [(ticker, pd.Timestamp(d)) for d in dates], names=["ticker", "date"]
    )
    return pd.DataFrame(
        {
            "open": [100.0] * len(dates),
            "high": [101.0] * len(dates),
            "low": [99.0] * len(dates),
            "close": [100.5] * len(dates),
            "adj_close": [100.5] * len(dates),
            "volume": [1_000_000] * len(dates),
        },
        index=idx,
    )


def test_refresh_writes_rows_and_updates_refresh_state(config, setup_db: Path) -> None:
    fake_provider = MagicMock()
    fake_provider.get_last_stored_date.return_value = None  # first run
    fake_provider.get_prices.return_value = _make_panel(
        "AAPL", ["2026-04-01", "2026-04-02"]
    )

    conn = sqlite3.connect(str(setup_db))
    try:
        result = refresh_prices(
            config,
            conn=conn,
            tickers=["AAPL"],
            today=date(2026, 4, 2),
            provider=fake_provider,
        )
        assert result["ok"] == 1
        assert result["failed"] == 0
        assert result["rows_written"] == 2

        n = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE ticker='AAPL'"
        ).fetchone()[0]
        assert n == 2

        rs = conn.execute(
            "SELECT status, last_value_text FROM refresh_state "
            "WHERE provider='yfinance' AND feed_type='ohlcv' AND ticker='AAPL'"
        ).fetchone()
        assert rs == ("OK", "2026-04-02")
    finally:
        conn.close()


def test_refresh_skips_when_already_current(config, setup_db: Path) -> None:
    fake_provider = MagicMock()
    fake_provider.get_last_stored_date.return_value = date(2026, 4, 2)

    conn = sqlite3.connect(str(setup_db))
    try:
        result = refresh_prices(
            config,
            conn=conn,
            tickers=["AAPL"],
            today=date(2026, 4, 2),
            provider=fake_provider,
        )
        assert result["skipped"] == 1
        assert result["ok"] == 0
        # get_prices NOT called — already current
        fake_provider.get_prices.assert_not_called()
    finally:
        conn.close()


def test_refresh_logs_and_continues_on_yfinance_error(config, setup_db: Path) -> None:
    """The daily run must complete even with per-ticker failures."""
    fake_provider = MagicMock()

    def by_ticker(t: str):
        return None  # both tickers are first-run

    fake_provider.get_last_stored_date.side_effect = by_ticker

    def get_prices(tickers, *args, **kwargs):
        if "BADTICK" in tickers:
            raise YFinanceError("bot detection")
        return _make_panel(tickers[0], ["2026-04-02"])

    fake_provider.get_prices.side_effect = get_prices

    conn = sqlite3.connect(str(setup_db))
    try:
        result = refresh_prices(
            config,
            conn=conn,
            tickers=["AAPL", "BADTICK"],
            today=date(2026, 4, 2),
            provider=fake_provider,
        )
        # AAPL succeeds, BADTICK fails — does NOT abort
        assert result["ok"] == 1
        assert result["failed"] == 1

        # Failed ticker has refresh_state row with FAILED status
        rs = conn.execute(
            "SELECT status, last_error FROM refresh_state WHERE ticker='BADTICK'"
        ).fetchone()
        assert rs[0] == "FAILED"
        assert "bot detection" in (rs[1] or "")
    finally:
        conn.close()


def test_refresh_uses_universe_and_benchmarks_by_default(config, setup_db: Path) -> None:
    conn = sqlite3.connect(str(setup_db))
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('AAPL', '2026-01-01', '2026-01-01:current', 0)"
    )
    conn.execute(
        "INSERT INTO benchmarks (ticker, category, last_updated) "
        "VALUES ('SPY', 'benchmark', 0)"
    )
    conn.commit()

    fake_provider = MagicMock()
    fake_provider.get_last_stored_date.return_value = None
    fake_provider.get_prices.side_effect = (
        lambda tickers, *a, **k: _make_panel(tickers[0], ["2026-04-01"])
    )

    try:
        result = refresh_prices(
            config,
            conn=conn,
            today=date(2026, 4, 1),
            provider=fake_provider,
        )
        # Both universe (AAPL) + benchmarks (SPY) fetched
        assert result["ok"] == 2
    finally:
        conn.close()


def test_refresh_excludes_delisted_universe_tickers(config, setup_db: Path) -> None:
    conn = sqlite3.connect(str(setup_db))
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, delisted_date, "
        "inclusion_window, last_updated) "
        "VALUES ('ENRN', '2020-01-01', '2025-01-01', '2020-01-01:2025-01-01', 0)"
    )
    conn.commit()

    fake_provider = MagicMock()
    fake_provider.get_last_stored_date.return_value = None
    fake_provider.get_prices.side_effect = (
        lambda tickers, *a, **k: _make_panel(tickers[0], ["2026-04-01"])
    )

    try:
        result = refresh_prices(
            config,
            conn=conn,
            today=date(2026, 4, 1),
            provider=fake_provider,
        )
        # Delisted ticker NOT fetched (would be wasteful — known to be 404)
        assert result["ok"] == 0
        fake_provider.get_prices.assert_not_called()
    finally:
        conn.close()
