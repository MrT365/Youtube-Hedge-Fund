"""refresh_short_interest tests — fake provider, no network.

Validates the canonical Plan-04 orchestrator pattern for the
short-interest daily snapshot (DATA-08):

  - one row per active universe ticker for `today` (PK ticker, snapshot_date)
  - INSERT OR IGNORE: same-day re-run is a no-op
  - provider returning ``None`` records ``status='SKIPPED'`` in refresh_state
  - YFinanceError per-ticker is log+continue with ``status='FAILED'`` +
    truncated last_error in refresh_state

All tests use a MagicMock-shaped fake provider — yfinance is never called.
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
from ls_equity_fund.data.providers.yfinance_provider import YFinanceError
from ls_equity_fund.data.short_interest import refresh_short_interest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_alembic_cfg(db_path: Path) -> AlembicConfig:
    """Build an AlembicConfig pointed at a tmp DB path.

    Mirrors tests/unit/data/test_phase1_migration.py — uses the repo's
    alembic.ini for script_location / file_template; overrides
    sqlalchemy.url to a tmp DB so production cache/ls_equity_fund.db is
    never touched.
    """
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def setup(tmp_path: Path, fresh_env_path: Path):
    """Tmp DB migrated to head + one active universe ticker (AAPL).

    Yields ``(config, conn)``. The connection is the same one the
    orchestrator will use (caller-owned path, owns_conn=False).
    Uses ``fresh_env_path`` from tests/conftest.py for Secrets validation.
    """
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


def test_refresh_writes_one_row_per_ticker_today(setup) -> None:
    """Happy path: provider returns a snapshot dict; orchestrator writes 1 row."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_short_interest.return_value = {
        "shares_short": 10_000_000.0,
        "short_ratio": 1.5,
        "short_percent_of_float": 0.05,
    }
    result = refresh_short_interest(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    assert result["ok"] == 1
    assert result["failed"] == 0
    assert result["rows_written"] == 1

    row = conn.execute(
        "SELECT shares_short, short_ratio, short_percent_of_float "
        "FROM short_interest WHERE ticker='AAPL' AND snapshot_date='2026-04-01'"
    ).fetchone()
    assert row == (10_000_000.0, 1.5, 0.05)

    rs = conn.execute(
        "SELECT status FROM refresh_state WHERE ticker='AAPL' AND feed_type='short_interest'"
    ).fetchone()
    assert rs[0] == "OK"


def test_idempotent_same_day(setup) -> None:
    """INSERT OR IGNORE on (ticker, snapshot_date) — second run = no-op."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_short_interest.return_value = {
        "shares_short": 1.0,
        "short_ratio": 1.0,
        "short_percent_of_float": 0.01,
    }
    refresh_short_interest(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    refresh_short_interest(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    n = conn.execute("SELECT COUNT(*) FROM short_interest").fetchone()[0]
    assert n == 1


def test_skipped_when_provider_returns_none(setup) -> None:
    """Provider None → status='SKIPPED', no row written."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_short_interest.return_value = None

    result = refresh_short_interest(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    assert result["rows_written"] == 0

    rs = conn.execute(
        "SELECT status, last_error FROM refresh_state "
        "WHERE ticker='AAPL' AND feed_type='short_interest'"
    ).fetchone()
    assert rs[0] == "SKIPPED"
    assert rs[1] == "no data"


def test_log_continue_on_yfinance_error(setup) -> None:
    """YFinanceError per ticker → status='FAILED', last_error truncated, no row."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_short_interest.side_effect = YFinanceError("rate limit hit")

    result = refresh_short_interest(
        config_obj,
        conn=conn,
        today=date(2026, 4, 1),
        provider=fake,
    )
    assert result["failed"] == 1
    assert result["rows_written"] == 0

    rs = conn.execute(
        "SELECT status, last_error FROM refresh_state "
        "WHERE ticker='AAPL' AND feed_type='short_interest'"
    ).fetchone()
    assert rs[0] == "FAILED"
    assert "rate limit" in (rs[1] or "")
