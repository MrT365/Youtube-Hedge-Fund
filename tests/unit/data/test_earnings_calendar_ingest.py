"""refresh_earnings_calendar tests — fake provider, no network.

Validates the orchestrator pattern for DATA-10 earnings-calendar refresh:

  - 0..N rows per ticker (one per upcoming event in the lookahead window)
  - PRE-INSERT PURGE of expired rows (expected_date < today) so the table
    does not grow unbounded with stale calendar entries
  - INSERT OR REPLACE keyed on (ticker, expected_date) so revisions to
    time_of_day / fiscal_period overwrite stale values
  - empty events list still counts as a successful per-ticker refresh
    (ok=1, rows_written=0)

Per PITFALLS D6: yfinance earnings dates are noisy. This module records what
yfinance reports; downstream Phase 5 applies a 5-day buffer.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.config import load_config
from ls_equity_fund.data.earnings_calendar import refresh_earnings_calendar

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_alembic_cfg(db_path: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def setup(tmp_path: Path, fresh_env_path: Path):
    db = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db)
    alembic_command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, "
        "last_updated) VALUES ('AAPL', '2026-01-01', '2026-01-01:current', 0)"
    )
    conn.commit()

    config_obj, _ = load_config(
        yaml_path=str(REPO_ROOT / "config.yaml.example"),
        env_path=fresh_env_path,
    )
    config_obj.data.cache_dir = str(tmp_path)
    yield config_obj, conn
    conn.close()


def test_refresh_writes_upcoming_earnings(setup) -> None:
    """Provider returns 1 upcoming event → 1 calendar row + ok=1."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_next_earnings_dates.return_value = [
        {
            "expected_date": "2026-04-25",
            "time_of_day": "AMC",
            "fiscal_period": "Q1",
        },
    ]

    result = refresh_earnings_calendar(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    assert result["ok"] == 1
    assert result["rows_written"] == 1

    row = conn.execute(
        "SELECT expected_date, time_of_day, fiscal_period "
        "FROM earnings_calendar WHERE ticker='AAPL'"
    ).fetchone()
    assert row == ("2026-04-25", "AMC", "Q1")


def test_purge_expired_dates_on_refresh(setup) -> None:
    """Pre-insert DELETE removes rows with expected_date < today."""
    config_obj, conn = setup
    # Pre-seed an expired earnings row.
    conn.execute(
        "INSERT INTO earnings_calendar (ticker, expected_date, refreshed_at) "
        "VALUES ('AAPL', '2025-12-15', 0)"
    )
    fake = MagicMock()
    fake.get_next_earnings_dates.return_value = []

    refresh_earnings_calendar(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )

    n = conn.execute(
        "SELECT COUNT(*) FROM earnings_calendar WHERE expected_date='2025-12-15'"
    ).fetchone()[0]
    assert n == 0  # expired row purged


def test_empty_events_means_no_upcoming(setup) -> None:
    """Empty event list → ticker still counted ok, but rows_written=0."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_next_earnings_dates.return_value = []

    result = refresh_earnings_calendar(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    assert result["ok"] == 1  # ticker processed
    assert result["rows_written"] == 0  # no upcoming earnings


def test_multiple_events_per_ticker(setup) -> None:
    """Provider returns 2 upcoming events → 2 calendar rows for the same ticker."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_next_earnings_dates.return_value = [
        {"expected_date": "2026-04-25", "time_of_day": "AMC"},
        {"expected_date": "2026-07-25", "time_of_day": "BMO"},
    ]

    result = refresh_earnings_calendar(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    assert result["rows_written"] == 2

    rows = list(
        conn.execute(
            "SELECT expected_date, time_of_day FROM earnings_calendar "
            "WHERE ticker='AAPL' ORDER BY expected_date"
        )
    )
    assert rows == [
        ("2026-04-25", "AMC"),
        ("2026-07-25", "BMO"),
    ]
