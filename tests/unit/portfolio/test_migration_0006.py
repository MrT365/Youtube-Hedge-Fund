"""Phase 5 migration tests (PORT-06)."""

from __future__ import annotations

import sqlite3


def test_phase5_tables_created(conn: sqlite3.Connection) -> None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"portfolio_positions", "portfolio_history", "position_approvals"} <= tables


def test_portfolio_positions_pk(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(portfolio_positions)")
    rows = cur.fetchall()
    pk_cols = sorted(c[1] for c in rows if c[5] > 0)
    assert pk_cols == ["side", "ticker"]


def test_portfolio_positions_check_side(conn: sqlite3.Connection) -> None:
    with conn, pytest_raises_check_constraint(conn):
        conn.execute(
            "INSERT INTO portfolio_positions ("
            "ticker, side, shares, entry_price, entry_date) "
            "VALUES ('AAPL', 'sideways', 1.0, 100.0, '2026-01-01')"
        )


def pytest_raises_check_constraint(conn: sqlite3.Connection):  # type: ignore[no-untyped-def]
    """Helper context manager — sqlite3.IntegrityError on CHECK violation."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():  # type: ignore[no-untyped-def]
        try:
            yield
        except sqlite3.IntegrityError:
            return
        raise AssertionError("expected sqlite3.IntegrityError on CHECK violation")

    return _ctx()


def test_position_approvals_pk(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(position_approvals)")
    rows = cur.fetchall()
    pk_cols = sorted(c[1] for c in rows if c[5] > 0)
    assert pk_cols == ["run_id", "side", "ticker"]


def test_portfolio_history_pk(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(portfolio_history)")
    rows = cur.fetchall()
    pk_cols = sorted(c[1] for c in rows if c[5] > 0)
    assert pk_cols == ["asof_date", "ticker"]


def test_indexes_exist(conn: sqlite3.Connection) -> None:
    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }
    assert {
        "idx_pp_sector",
        "idx_pp_side",
        "idx_ph_asof",
        "idx_ph_ticker",
        "idx_pa_asof",
        "idx_pa_run",
    } <= indexes
