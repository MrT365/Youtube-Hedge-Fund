"""Phase 1 migration unit tests — schema integrity (D-04 source of truth).

Each test programmatically applies migration 0002 against a tmp_path SQLite DB
and asserts the schema invariants this plan binds:

  - All 13 Phase 1 tables present after `alembic upgrade head` (binds plan SC1).
  - universe carries the PIT triplet first_seen_date / delisted_date / inclusion_window
    (binds CP1 / SC1).
  - insider_transactions.transaction_code is NOT NULL with a CHECK constraint that
    rejects unknown codes (binds CP3 / SC3).
  - fundamentals.as_of_ingest_date is part of the primary key (D2 mitigation).
  - downgrade rolls back to the Phase 0 baseline (only runs / heartbeat / alembic_version).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_TABLES = {
    "universe",
    "benchmarks",
    "daily_prices",
    "fundamentals",
    "fundamental_ratios",
    "filings_metadata",
    "insider_transactions",
    "institutional_holdings",
    "short_interest",
    "analyst_estimates",
    "earnings_calendar",
    "macro_calendar",
    "refresh_state",
}


def _make_alembic_cfg(db_path: Path) -> AlembicConfig:
    """Build an AlembicConfig pointed at a tmp DB path.

    Uses the repo's alembic.ini for script_location / file_template; overrides
    sqlalchemy.url to a tmp DB so production cache/ls_equity_fund.db is never
    touched (mirrors tests/unit/test_migrations.py pattern).
    """
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    alembic_command.upgrade(cfg, "head")
    return db_path


def test_all_phase1_tables_created(migrated_db: Path) -> None:
    """SC1: every Phase 1 table is present after `alembic upgrade head`."""
    conn = sqlite3.connect(str(migrated_db))
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {sorted(missing)}"


def test_universe_has_pit_columns(migrated_db: Path) -> None:
    """Binds CP1 / SC1 — first_seen_date, delisted_date, inclusion_window all present."""
    conn = sqlite3.connect(str(migrated_db))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(universe)")}
    finally:
        conn.close()
    assert {"first_seen_date", "delisted_date", "inclusion_window"} <= cols


def test_insider_transaction_code_first_class(migrated_db: Path) -> None:
    """Binds CP3 / SC3 — transaction_code is its own NOT NULL column."""
    conn = sqlite3.connect(str(migrated_db))
    try:
        rows = list(conn.execute("PRAGMA table_info(insider_transactions)"))
    finally:
        conn.close()
    code_col = next((r for r in rows if r[1] == "transaction_code"), None)
    assert code_col is not None, "transaction_code column missing"
    # PRAGMA table_info row layout: (cid, name, type, notnull, dflt_value, pk)
    assert code_col[3] == 1, "transaction_code must be NOT NULL"


def test_insider_check_constraint_rejects_unknown_code(migrated_db: Path) -> None:
    """Binds CP3 — only P/S/A/M/F/G/D codes accepted at the schema layer."""
    conn = sqlite3.connect(str(migrated_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO insider_transactions ("
                "  accession_number, line_no, ticker, transaction_code, "
                "  transaction_date, filed_date"
                ") VALUES ('test', 1, 'AAPL', 'X', '2026-01-01', '2026-01-01')"
            )
    finally:
        conn.close()


def test_fundamentals_pk_includes_as_of_ingest_date(migrated_db: Path) -> None:
    """Binds D2 mitigation — append-only restated fundamentals via composite PK."""
    conn = sqlite3.connect(str(migrated_db))
    try:
        rows = list(conn.execute("PRAGMA table_info(fundamentals)"))
    finally:
        conn.close()
    # PRAGMA table_info row layout: (cid, name, type, notnull, dflt_value, pk)
    # pk column == 0 means "not in PK"; pk column > 0 is the 1-based PK position.
    pk_cols = {r[1] for r in rows if r[5] > 0}
    assert pk_cols == {"ticker", "period_end", "period_type", "as_of_ingest_date"}, (
        f"fundamentals PK is {sorted(pk_cols)}, expected the 4-tuple including as_of_ingest_date"
    )


def test_downgrade_drops_phase1_tables(tmp_path: Path) -> None:
    """`alembic downgrade 0001` removes Phase 1 tables and keeps Phase 0 intact."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_cfg(db_path)
    alembic_command.upgrade(cfg, "head")
    alembic_command.downgrade(cfg, "0001")

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    # All Phase 1 tables removed.
    assert not (EXPECTED_TABLES & tables), (
        f"Phase 1 tables remain after downgrade: {sorted(EXPECTED_TABLES & tables)}"
    )
    # Phase 0 baseline retained.
    assert {"runs", "heartbeat", "alembic_version"} <= tables, (
        f"Phase 0 tables missing after downgrade: got {sorted(tables)}"
    )
