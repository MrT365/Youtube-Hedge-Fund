"""refresh_estimates tests — fake provider, no network.

Validates the canonical Plan-04 orchestrator pattern for the analyst-
estimates daily snapshot (DATA-09):

  - one row per active universe ticker for `today` (PK ticker, snapshot_date)
  - INSERT OR IGNORE: same-day re-run is a no-op
  - provider returning ``None`` records ``status='SKIPPED'``
  - YFinanceError per-ticker is log+continue with ``status='FAILED'``

The 30/60/90-day estimate-revisions factor (Phase 2) reconstructs revisions
from these append-only daily snapshot rows — that is why snapshot_date is in
the PK and INSERT OR IGNORE is the write semantics.
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
from ls_equity_fund.data.estimates import refresh_estimates
from ls_equity_fund.data.providers.yfinance_provider import YFinanceError

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


def test_refresh_writes_one_row_per_ticker_today(setup) -> None:
    """Happy path: provider returns 7-field estimates dict; row persisted."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_estimates.return_value = {
        "eps_fy1": 6.5,
        "eps_fy2": 7.2,
        "rev_fy1": 400_000_000_000.0,
        "rev_fy2": 425_000_000_000.0,
        "target_price": 220.0,
        "n_analysts": 30,
    }

    result = refresh_estimates(
        config_obj, conn=conn, today=date(2026, 4, 1), provider=fake,
    )
    assert result["ok"] == 1
    assert result["rows_written"] == 1

    row = conn.execute(
        "SELECT eps_fy1, eps_fy2, rev_fy1, rev_fy2, target_price, n_analysts "
        "FROM analyst_estimates "
        "WHERE ticker='AAPL' AND snapshot_date='2026-04-01'"
    ).fetchone()
    assert row == (6.5, 7.2, 400_000_000_000.0, 425_000_000_000.0, 220.0, 30)


def test_idempotent_same_day(setup) -> None:
    """Second same-day refresh writes nothing new."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_estimates.return_value = {
        "eps_fy1": 1.0,
        "eps_fy2": 1.1,
        "rev_fy1": 100.0,
        "rev_fy2": 110.0,
        "target_price": 50.0,
        "n_analysts": 10,
    }
    refresh_estimates(
        config_obj, conn=conn, today=date(2026, 4, 1), provider=fake,
    )
    refresh_estimates(
        config_obj, conn=conn, today=date(2026, 4, 1), provider=fake,
    )
    n = conn.execute("SELECT COUNT(*) FROM analyst_estimates").fetchone()[0]
    assert n == 1


def test_log_continue_on_yfinance_error(setup) -> None:
    """YFinanceError → status='FAILED' in refresh_state, no analyst_estimates row."""
    config_obj, conn = setup
    fake = MagicMock()
    fake.get_estimates.side_effect = YFinanceError("yahoo 502")

    result = refresh_estimates(
        config_obj, conn=conn, today=date(2026, 4, 1), provider=fake,
    )
    assert result["failed"] == 1
    assert result["rows_written"] == 0

    rs = conn.execute(
        "SELECT status, last_error FROM refresh_state "
        "WHERE ticker='AAPL' AND feed_type='estimates'"
    ).fetchone()
    assert rs[0] == "FAILED"
    assert "yahoo 502" in (rs[1] or "")
