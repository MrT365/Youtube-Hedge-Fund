"""Rebalance schedule advisory tests (PORT-05 / SC4)."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta

from ls_equity_fund.portfolio.schedule import (
    evaluate_schedule,
    find_earnings_within_window,
    find_fomc_within_window,
    opex_within_window,
    third_friday,
)


def _seed_earnings(conn: sqlite3.Connection, ticker: str, expected: date) -> None:
    with conn:
        conn.execute(
            "INSERT INTO earnings_calendar (ticker, expected_date, fiscal_period, refreshed_at) "
            "VALUES (?, ?, ?, ?)",
            (ticker, expected.isoformat(), "Q4", int(time.time())),
        )


def _seed_macro(
    conn: sqlite3.Connection, event_id: str, event_date: date, kind: str = "FOMC"
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO macro_calendar (event_id, event_type, event_date_et, "
            "description, source, fetched_at, last_refreshed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                kind,
                event_date.isoformat(),
                "test",
                "test",
                int(time.time()),
                int(time.time()),
            ),
        )


def test_third_friday_january_2026() -> None:
    assert third_friday(2026, 1) == date(2026, 1, 16)


def test_third_friday_february_2026() -> None:
    assert third_friday(2026, 2) == date(2026, 2, 20)


def test_opex_within_window_hits_when_close() -> None:
    asof = date(2026, 1, 14)  # Wed; OPEX on Fri Jan 16
    assert opex_within_window(asof, days=3) == date(2026, 1, 16)


def test_opex_within_window_misses_when_far() -> None:
    asof = date(2026, 1, 1)
    assert opex_within_window(asof, days=3) is None


def test_opex_rolls_into_next_month() -> None:
    """Last day of January — current month's OPEX past, next month should roll in."""
    asof = date(2026, 1, 30)
    # Next OPEX is 2026-02-20, 21 days away → 3-day window misses, 30-day catches.
    assert opex_within_window(asof, days=3) is None
    assert opex_within_window(asof, days=30) == date(2026, 2, 20)


def test_find_earnings_within_window(conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 1)
    _seed_earnings(conn, "AAA", asof + timedelta(days=1))  # in
    _seed_earnings(conn, "BBB", asof + timedelta(days=10))  # out
    found = find_earnings_within_window(conn, tickers=["AAA", "BBB"], asof=asof, days=2)
    assert ("AAA", (asof + timedelta(days=1)).isoformat()) in found
    assert all(t != "BBB" for t, _ in found)


def test_find_fomc_within_window(conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 1)
    _seed_macro(conn, "fomc-2026-05-04", asof + timedelta(days=3))
    _seed_macro(conn, "cpi-2026-05-15", asof + timedelta(days=14), kind="CPI")
    fomc = find_fomc_within_window(conn, asof=asof, days=5)
    assert asof + timedelta(days=3) in fomc
    # CPI events are not FOMC.
    assert all(d != asof + timedelta(days=14) for d in fomc)


def test_evaluate_schedule_aggregates_warnings(conn: sqlite3.Connection) -> None:
    # Use a date close to OPEX so the third-Friday check fires too.
    asof = date(2026, 5, 13)  # Wed; opex 2026-05-15 = 2d away
    _seed_earnings(conn, "AAA", asof + timedelta(days=1))
    _seed_macro(conn, "fomc-x", asof + timedelta(days=4))
    adv = evaluate_schedule(conn, asof=asof, candidate_tickers=["AAA"])
    codes = {a.code for a in adv.items}
    assert "earnings_within_2d" in codes
    assert "fomc_within_5d" in codes
    # 2026-05-15 is the third Friday of May 2026.
    assert "opex_within_3d" in codes
    # All advisories are warnings, never blocks (PORT-05).
    assert all(a.severity == "warn" for a in adv.items)


def test_evaluate_schedule_empty_when_clean(conn: sqlite3.Connection) -> None:
    asof = date(2026, 6, 3)  # Wed, no OPEX, no FOMC, no earnings
    adv = evaluate_schedule(conn, asof=asof, candidate_tickers=["AAA"])
    assert adv.warnings == []
