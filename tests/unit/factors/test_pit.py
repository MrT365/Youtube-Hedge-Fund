"""Tests for shared point-in-time factor helpers."""

from __future__ import annotations

import sqlite3
from datetime import date

from ls_equity_fund.factors._pit import (
    latest_close_pit,
    latest_estimates_pit,
    latest_fundamentals_pit,
)


def _insert_fundamental(
    conn: sqlite3.Connection,
    *,
    ticker: str = "AAPL",
    period_end: str = "2025-12-31",
    period_type: str = "annual",
    as_of_ingest_date: str = "2026-01-15",
    revenue: float = 100.0,
) -> None:
    conn.execute(
        """
        INSERT INTO fundamentals (
            ticker, period_end, period_type, as_of_ingest_date, revenue
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (ticker, period_end, period_type, as_of_ingest_date, revenue),
    )


def test_latest_annual_pit_correct(migrated_conn: sqlite3.Connection) -> None:
    _insert_fundamental(migrated_conn, as_of_ingest_date="2026-01-15", revenue=100.0)
    _insert_fundamental(migrated_conn, as_of_ingest_date="2026-04-01", revenue=110.0)

    early = latest_fundamentals_pit(migrated_conn, "AAPL", "annual", date(2026, 2, 1))
    late = latest_fundamentals_pit(migrated_conn, "AAPL", "annual", date(2026, 5, 1))

    assert early[0]["revenue"] == 100.0
    assert late[0]["revenue"] == 110.0


def test_only_returns_periods_at_or_before_asof(migrated_conn: sqlite3.Connection) -> None:
    _insert_fundamental(
        migrated_conn,
        period_end="2026-06-30",
        as_of_ingest_date="2026-07-15",
        revenue=200.0,
    )

    rows = latest_fundamentals_pit(migrated_conn, "AAPL", "quarterly", date(2026, 7, 1))

    assert rows == []


def test_n_rows_limit(migrated_conn: sqlite3.Connection) -> None:
    for idx, period_end in enumerate(
        ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]
    ):
        _insert_fundamental(
            migrated_conn,
            period_end=period_end,
            period_type="quarterly",
            revenue=float(idx),
        )

    rows = latest_fundamentals_pit(
        migrated_conn,
        "AAPL",
        "quarterly",
        date(2026, 5, 1),
        n=3,
    )

    assert [r["period_end"] for r in rows] == ["2026-03-31", "2025-12-31", "2025-09-30"]


def test_returns_empty_for_missing_ticker(migrated_conn: sqlite3.Connection) -> None:
    assert latest_fundamentals_pit(migrated_conn, "NOPE", "annual", date(2026, 5, 1)) == []


def test_no_rows_when_asof_too_early(migrated_conn: sqlite3.Connection) -> None:
    _insert_fundamental(migrated_conn, as_of_ingest_date="2026-04-01")
    assert latest_fundamentals_pit(migrated_conn, "AAPL", "annual", date(2026, 1, 1)) == []


def test_latest_estimates_pit(migrated_conn: sqlite3.Connection) -> None:
    migrated_conn.executemany(
        "INSERT INTO analyst_estimates (ticker, snapshot_date, eps_fy1) VALUES (?, ?, ?)",
        [("AAPL", "2026-04-01", 5.0), ("AAPL", "2026-04-15", 5.5)],
    )

    row = latest_estimates_pit(migrated_conn, "AAPL", date(2026, 4, 10))

    assert row is not None
    assert row["eps_fy1"] == 5.0


def test_estimates_returns_none_when_no_snapshot(migrated_conn: sqlite3.Connection) -> None:
    assert latest_estimates_pit(migrated_conn, "AAPL", date(2026, 4, 10)) is None


def test_latest_close_pit(migrated_conn: sqlite3.Connection) -> None:
    migrated_conn.executemany(
        "INSERT INTO daily_prices (ticker, date, close) VALUES (?, ?, ?)",
        [("AAPL", "2026-04-01", 100.0), ("AAPL", "2026-04-15", 105.0)],
    )

    assert latest_close_pit(migrated_conn, "AAPL", date(2026, 4, 10)) == 100.0


def test_latest_close_none_when_no_data(migrated_conn: sqlite3.Connection) -> None:
    assert latest_close_pit(migrated_conn, "AAPL", date(2026, 4, 10)) is None
