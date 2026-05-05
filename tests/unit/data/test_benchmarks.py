"""Benchmarks refresh tests (Plan 01-03 / DATA-02).

Six unit tests cover:
1. Refresh writes 18 rows total (4 benchmarks + 11 sector_etfs + 3 macro)
2. Each ticker carries the correct `category` value
3. Re-running is idempotent — INSERT OR REPLACE keeps row count at 18
4. All 11 spec-default sector ETFs land with category='sector_etf'
5. Custom (unknown) tickers get an empty description without crashing
6. The schema CHECK constraint rejects an invalid category at the DB layer

The fixture migrates a fresh tmp SQLite via Alembic so the `benchmarks` table
shape matches migration 0002 exactly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.config import load_config
from ls_equity_fund.data.benchmarks import refresh_benchmarks

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def migrated_conn(tmp_path: Path):
    db = tmp_path / "test.db"
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    alembic_command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def config(fresh_yaml_path: Path, fresh_env_path: Path):
    cfg, _ = load_config(yaml_path=fresh_yaml_path, env_path=fresh_env_path)
    return cfg


def test_refresh_writes_18_rows(migrated_conn, config) -> None:
    counts = refresh_benchmarks(config, conn=migrated_conn)
    assert counts == {"benchmark": 4, "sector_etf": 11, "macro": 3}
    total = migrated_conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
    assert total == 18


def test_refresh_categorizes_correctly(migrated_conn, config) -> None:
    refresh_benchmarks(config, conn=migrated_conn)
    spy = migrated_conn.execute("SELECT category FROM benchmarks WHERE ticker='SPY'").fetchone()
    assert spy["category"] == "benchmark"
    xlk = migrated_conn.execute("SELECT category FROM benchmarks WHERE ticker='XLK'").fetchone()
    assert xlk["category"] == "sector_etf"
    vix = migrated_conn.execute("SELECT category FROM benchmarks WHERE ticker='^VIX'").fetchone()
    assert vix["category"] == "macro"


def test_refresh_is_idempotent(migrated_conn, config) -> None:
    refresh_benchmarks(config, conn=migrated_conn)
    refresh_benchmarks(config, conn=migrated_conn)  # run twice
    total = migrated_conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
    assert total == 18  # not 36


def test_refresh_includes_all_11_sector_etfs(migrated_conn, config) -> None:
    refresh_benchmarks(config, conn=migrated_conn)
    rows = migrated_conn.execute(
        "SELECT ticker FROM benchmarks WHERE category='sector_etf' ORDER BY ticker"
    ).fetchall()
    sectors = {r["ticker"] for r in rows}
    assert sectors == {
        "XLK",
        "XLF",
        "XLV",
        "XLE",
        "XLI",
        "XLC",
        "XLY",
        "XLP",
        "XLB",
        "XLRE",
        "XLU",
    }


def test_refresh_handles_custom_ticker_without_description(migrated_conn, config) -> None:
    config.data.sector_etfs = [*config.data.sector_etfs, "SMH"]  # add semis ETF
    refresh_benchmarks(config, conn=migrated_conn)
    smh = migrated_conn.execute("SELECT description FROM benchmarks WHERE ticker='SMH'").fetchone()
    assert smh["description"] == ""  # unknown tickers get empty description, no crash


def test_refresh_check_constraint_rejects_invalid_category(migrated_conn) -> None:
    """Schema-level guard."""
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO benchmarks (ticker, category, last_updated) "
            "VALUES ('TEST', 'invalid_category', 0)"
        )
