"""Fundamentals refresh tests including D2 mitigation (append-only).

D2 (PITFALLS.md, CRITICAL): yfinance returns the most-recent restated
fundamentals. v1 mitigation is APPEND-ONLY ingest keyed by today's
``as_of_ingest_date``. The bind test below exercises that contract directly.
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
from ls_equity_fund.data.fundamentals import refresh_fundamentals
from ls_equity_fund.data.providers.yfinance_provider import YFinanceError

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def setup_db(tmp_path: Path):
    db = tmp_path / "test.db"
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    alembic_command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('AAPL', '2026-01-01', '2026-01-01:current', 0)"
    )
    conn.commit()
    yield db, conn
    conn.close()


@pytest.fixture
def config(setup_db, fresh_env_path, monkeypatch):
    """Load the example config; redirect cache_dir to tmp_path."""
    monkeypatch.chdir(REPO_ROOT)
    config_obj, _ = load_config(
        yaml_path=REPO_ROOT / "config.yaml.example",
        env_path=fresh_env_path,
    )
    config_obj.data.cache_dir = str(setup_db[0].parent)
    return config_obj


def _make_fundamentals_df():
    return pd.DataFrame(
        [
            {"period_end": "2025-12-31", "period_type": "annual",
             "revenue": 100.0, "net_income": 20.0, "cfo": 25.0, "accruals": -5.0,
             "total_assets": 500.0},
            {"period_end": "2026-03-31", "period_type": "quarterly",
             "revenue": 30.0, "net_income": 6.0, "cfo": 7.5},
        ]
    ).set_index(["period_end", "period_type"])


def test_refresh_writes_with_today_as_ingest_date(setup_db, config) -> None:
    _, conn = setup_db
    fake = MagicMock()
    fake.get_fundamentals.return_value = _make_fundamentals_df()

    result = refresh_fundamentals(
        config, conn=conn, today=date(2026, 4, 1), provider=fake,
    )
    assert result["ok"] == 1
    assert result["rows_written"] == 2

    rows = conn.execute(
        "SELECT period_end, period_type, as_of_ingest_date, revenue "
        "FROM fundamentals WHERE ticker='AAPL' ORDER BY period_end"
    ).fetchall()
    assert len(rows) == 2
    assert all(r[2] == "2026-04-01" for r in rows)  # today's date
    # Order: 2025-12-31 (annual) before 2026-03-31 (quarterly)
    assert rows[0][3] == 100.0


def test_d2_mitigation_appends_on_restated_rerun(setup_db, config) -> None:
    """D2 binding — restated values write a NEW row, original is preserved.

    PITFALLS.md D2: yfinance returns the most-recent restated income
    statement. If we let a re-ingest UPDATE / REPLACE the row, every backtest
    after the restate sees the post-restatement number — silent look-ahead.
    Append-only with as_of_ingest_date in the PK protects against that.
    """
    _, conn = setup_db
    fake = MagicMock()

    # First ingest: April 1 — original revenue
    fake.get_fundamentals.return_value = pd.DataFrame(
        [{"period_end": "2025-12-31", "period_type": "annual", "revenue": 100.0}]
    ).set_index(["period_end", "period_type"])
    refresh_fundamentals(config, conn=conn, today=date(2026, 4, 1), provider=fake)

    # Second ingest: November 1 — same period_end, RESTATED revenue
    fake.get_fundamentals.return_value = pd.DataFrame(
        [{"period_end": "2025-12-31", "period_type": "annual", "revenue": 110.0}]
    ).set_index(["period_end", "period_type"])
    refresh_fundamentals(config, conn=conn, today=date(2026, 11, 1), provider=fake)

    rows = conn.execute(
        "SELECT as_of_ingest_date, revenue FROM fundamentals "
        "WHERE ticker='AAPL' AND period_end='2025-12-31' "
        "ORDER BY as_of_ingest_date"
    ).fetchall()
    assert len(rows) == 2  # NOT 1 — original preserved
    assert rows[0] == ("2026-04-01", 100.0)  # original
    assert rows[1] == ("2026-11-01", 110.0)  # restated

    # PIT-aware query: as of Apr 15, the row reads 100.0 (the original)
    pit_apr = conn.execute(
        "SELECT revenue FROM fundamentals "
        "WHERE ticker='AAPL' AND period_end='2025-12-31' "
        "AND as_of_ingest_date <= '2026-04-15' "
        "ORDER BY as_of_ingest_date DESC LIMIT 1"
    ).fetchone()
    assert pit_apr == (100.0,)


def test_same_day_rerun_is_idempotent(setup_db, config) -> None:
    """Same as_of_ingest_date PK — INSERT OR IGNORE skips duplicates."""
    _, conn = setup_db
    fake = MagicMock()
    fake.get_fundamentals.return_value = _make_fundamentals_df()
    refresh_fundamentals(config, conn=conn, today=date(2026, 4, 1), provider=fake)
    refresh_fundamentals(config, conn=conn, today=date(2026, 4, 1), provider=fake)
    n = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
    assert n == 2  # not 4


def test_log_and_continue_on_provider_error(setup_db, config) -> None:
    _, conn = setup_db
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('BAD', '2026-01-01', '2026-01-01:current', 0)"
    )
    conn.commit()
    fake = MagicMock()

    def fundamentals_by_ticker(t):
        if t == "BAD":
            raise YFinanceError("upstream broken")
        return _make_fundamentals_df()

    fake.get_fundamentals.side_effect = fundamentals_by_ticker
    result = refresh_fundamentals(
        config, conn=conn, today=date(2026, 4, 1), provider=fake,
    )
    assert result["ok"] == 1
    assert result["failed"] == 1
    rs = conn.execute(
        "SELECT status FROM refresh_state WHERE ticker='BAD' AND feed_type='fundamentals'"
    ).fetchone()
    assert rs[0] == "FAILED"


def test_excludes_delisted_tickers(setup_db, config) -> None:
    """Survivorship-bias guard inverted — delisted tickers are excluded from
    a forward-looking refresh (they no longer trade), but their HISTORICAL
    rows in the universe table are preserved (D1 mitigation in Plan 02).
    """
    _, conn = setup_db
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, delisted_date, "
        "inclusion_window, last_updated) "
        "VALUES ('ENRN', '2020-01-01', '2025-01-01', '2020:2025', 0)"
    )
    conn.commit()
    fake = MagicMock()
    fake.get_fundamentals.return_value = _make_fundamentals_df()
    refresh_fundamentals(config, conn=conn, today=date(2026, 4, 1), provider=fake)
    # ENRN not fetched
    enrn_rows = conn.execute(
        "SELECT * FROM fundamentals WHERE ticker='ENRN'"
    ).fetchall()
    assert enrn_rows == []
    # AAPL was fetched
    assert fake.get_fundamentals.call_count == 1


def test_append_only_no_replace_in_source() -> None:
    """Source-level guard: the orchestrator MUST NOT use INSERT OR REPLACE
    against the fundamentals table. That would silently destroy D2 mitigation.
    """
    src = (REPO_ROOT / "src/ls_equity_fund/data/fundamentals.py").read_text()
    assert "INSERT OR IGNORE INTO fundamentals" in src
    # No INSERT OR REPLACE / UPDATE against fundamentals table
    lower = src.lower()
    # refresh_state (DATA-12) legitimately uses INSERT OR REPLACE — narrow
    # the check to fundamentals.
    assert "insert or replace into fundamentals" not in lower
    assert "update fundamentals" not in lower
