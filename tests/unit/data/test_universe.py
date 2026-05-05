"""Universe-builder tests — PIT correctness binds CP1.

The survivorship-prevention contract is the load-bearing test:
``test_merge_flags_delisted_does_not_delete``. If that test passes, every
downstream factor + backtest is at least architecturally protected from the
look-back-only-survivors trap (D1 in PITFALLS.md).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.data.universe import _build_sp500, merge_universe_pit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"
FIXTURE_HTML = REPO_ROOT / "tests" / "fixtures" / "sp500_wikipedia_fixture.html"


@pytest.fixture
def migrated_conn(tmp_path: Path):
    """Fresh SQLite, alembic upgraded to head; closed on teardown."""
    db_path = tmp_path / "test.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    alembic_command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_sp500_builder_parses_wikipedia_fixture() -> None:
    """sp500 mode parses the Wikipedia fixture HTML into 5 ticker rows."""
    rows = _build_sp500(fixture_html_path=FIXTURE_HTML)
    assert len(rows) == 5
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"AAPL", "MSFT", "NVDA", "JPM", "JNJ"}
    apple = next(r for r in rows if r["ticker"] == "AAPL")
    assert apple["sector"] == "Information Technology"
    assert apple["company_name"] == "Apple Inc."


def test_merge_inserts_new_tickers_with_first_seen_date(migrated_conn) -> None:
    """First INSERT sets first_seen_date=today, delisted_date=NULL,
    inclusion_window='{today}:current'.
    """
    rows = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Tech"}]
    stats = merge_universe_pit(rows, migrated_conn, today=date(2026, 1, 15))
    assert stats == {"inserted": 1, "updated": 0, "delisted": 0, "reincluded": 0}
    row = migrated_conn.execute(
        "SELECT first_seen_date, delisted_date, inclusion_window FROM universe WHERE ticker='AAPL'"
    ).fetchone()
    assert row["first_seen_date"] == "2026-01-15"
    assert row["delisted_date"] is None
    assert row["inclusion_window"] == "2026-01-15:current"


def test_merge_preserves_first_seen_on_rerun(migrated_conn) -> None:
    """A re-run on a later day must NOT bump first_seen_date — that would
    invalidate every PIT query that filters on it."""
    rows = [{"ticker": "AAPL", "company_name": "Apple", "sector": "Tech"}]
    merge_universe_pit(rows, migrated_conn, today=date(2026, 1, 15))
    rows[0]["company_name"] = "Apple Inc."  # metadata updated
    stats = merge_universe_pit(rows, migrated_conn, today=date(2026, 2, 1))
    assert stats == {"inserted": 0, "updated": 1, "delisted": 0, "reincluded": 0}
    row = migrated_conn.execute(
        "SELECT first_seen_date, company_name FROM universe WHERE ticker='AAPL'"
    ).fetchone()
    assert row["first_seen_date"] == "2026-01-15"
    assert row["company_name"] == "Apple Inc."


def test_merge_flags_delisted_does_not_delete(migrated_conn) -> None:
    """SC1 / CP1 — the survivorship-bias-prevention contract.

    Day 1: 3 tickers active. Day 30: 1 ticker no longer in incoming list.
    The missing ticker MUST remain in the table with delisted_date set.
    """
    day1 = [{"ticker": t, "sector": "X"} for t in ["AAPL", "MSFT", "ENRN"]]
    merge_universe_pit(day1, migrated_conn, today=date(2026, 1, 1))
    assert migrated_conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0] == 3

    day30 = [{"ticker": t, "sector": "X"} for t in ["AAPL", "MSFT"]]
    stats = merge_universe_pit(day30, migrated_conn, today=date(2026, 1, 30))
    assert stats["delisted"] == 1
    assert stats["updated"] == 2

    enrn = migrated_conn.execute(
        "SELECT first_seen_date, delisted_date, inclusion_window FROM universe WHERE ticker='ENRN'"
    ).fetchone()
    assert enrn is not None, "delisted ticker must NOT be deleted (CP1)"
    assert enrn["first_seen_date"] == "2026-01-01"
    assert enrn["delisted_date"] == "2026-01-30"
    assert enrn["inclusion_window"] == "2026-01-01:2026-01-30"

    # Total row count UNCHANGED — flagged, not deleted.
    assert migrated_conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0] == 3


def test_pit_query_at_date_excludes_post_delisted(migrated_conn) -> None:
    """PIT-query convention from the module docstring.

    Universe at date D = {tickers where first_seen_date <= D AND
    (delisted_date IS NULL OR delisted_date > D)}.
    """
    merge_universe_pit(
        [{"ticker": "ENRN", "sector": "X"}],
        migrated_conn,
        today=date(2026, 1, 1),
    )
    merge_universe_pit([], migrated_conn, today=date(2026, 6, 1))  # delist all

    universe_march = migrated_conn.execute(
        "SELECT ticker FROM universe "
        "WHERE first_seen_date <= ? AND (delisted_date IS NULL OR delisted_date > ?)",
        ("2026-03-01", "2026-03-01"),
    ).fetchall()
    assert {r[0] for r in universe_march} == {"ENRN"}, "ENRN active on 2026-03-01"

    universe_dec = migrated_conn.execute(
        "SELECT ticker FROM universe "
        "WHERE first_seen_date <= ? AND (delisted_date IS NULL OR delisted_date > ?)",
        ("2026-12-01", "2026-12-01"),
    ).fetchall()
    assert universe_dec == [], "ENRN already delisted (2026-06-01) on 2026-12-01"


def test_reincluded_ticker_keeps_original_first_seen(migrated_conn) -> None:
    """Re-listing: delisted_date clears back to NULL, first_seen_date PRESERVED
    as the original date, inclusion_window resets to '{first_seen}:current'."""
    rows = [{"ticker": "T", "sector": "X"}]
    merge_universe_pit(rows, migrated_conn, today=date(2026, 1, 1))
    merge_universe_pit([], migrated_conn, today=date(2026, 3, 1))  # delist
    stats = merge_universe_pit(rows, migrated_conn, today=date(2026, 6, 1))
    assert stats["reincluded"] == 1
    row = migrated_conn.execute(
        "SELECT first_seen_date, delisted_date, inclusion_window FROM universe WHERE ticker='T'"
    ).fetchone()
    assert row["first_seen_date"] == "2026-01-01"  # ORIGINAL preserved
    assert row["delisted_date"] is None  # cleared
    assert row["inclusion_window"] == "2026-01-01:current"
