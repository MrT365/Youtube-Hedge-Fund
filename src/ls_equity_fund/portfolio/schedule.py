"""Rebalance schedule advisory (PORT-05).

Returns advisory warnings only — does NOT block trading. The pre-trade veto
(Phase 6) is the absolute layer that can refuse a trade. This module exists
purely to surface "are you sure you want to rebalance today?" hints to the
operator before they kick off ``run-execution``.

Three checks:

  * earnings_within_2d  — any current or proposed position has an earnings
    event in the next 2 trading days
  * fomc_within_5d      — Federal Reserve FOMC meeting is within 5 calendar
    days (read from the L1 ``macro_calendar`` table populated by
    ``data/fomc_calendar.py``)
  * opex_within_3d      — monthly options expiration falls within 3 calendar
    days (third Friday of the calendar month — computed in-process so we
    don't need an external feed for it)
"""

from __future__ import annotations

import calendar
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date as date_type
from datetime import timedelta


@dataclass(frozen=True)
class Advisory:
    """One advisory warning."""

    code: str
    message: str
    severity: str  # 'info' | 'warn'


@dataclass(frozen=True)
class ScheduleAdvisories:
    """Aggregate advisory result for ``run-portfolio``."""

    asof: date_type
    items: list[Advisory]

    @property
    def warnings(self) -> list[Advisory]:
        return [a for a in self.items if a.severity == "warn"]


# -----------------------------------------------------------------------------
# Individual checks
# -----------------------------------------------------------------------------


def find_earnings_within_window(
    conn: sqlite3.Connection,
    *,
    tickers: Iterable[str],
    asof: date_type,
    days: int,
) -> list[tuple[str, str]]:
    """Return ``(ticker, expected_date)`` for tickers w/ earnings in window.

    Window is asof <= expected_date <= asof + days (calendar days). Empty
    iterable returns an empty list.
    """
    tickers = list(tickers)
    if not tickers:
        return []
    placeholders = ",".join("?" * len(tickers))
    cur = conn.execute(
        f"""
        SELECT ticker, expected_date
        FROM earnings_calendar
        WHERE ticker IN ({placeholders})
          AND expected_date BETWEEN ? AND ?
        ORDER BY expected_date, ticker
        """,
        [*tickers, asof.isoformat(), (asof + timedelta(days=days)).isoformat()],
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def find_fomc_within_window(
    conn: sqlite3.Connection, *, asof: date_type, days: int
) -> list[date_type]:
    """Return FOMC dates inside [asof, asof+days]. Reads ``macro_calendar``."""
    cur = conn.execute(
        """
        SELECT event_date_et
        FROM macro_calendar
        WHERE event_type = 'FOMC'
          AND event_date_et BETWEEN ? AND ?
        ORDER BY event_date_et
        """,
        [asof.isoformat(), (asof + timedelta(days=days)).isoformat()],
    )
    out: list[date_type] = []
    for (date_str,) in cur.fetchall():
        try:
            out.append(date_type.fromisoformat(date_str))
        except (TypeError, ValueError):
            continue
    return out


def third_friday(year: int, month: int) -> date_type:
    """Return the third Friday of ``year``-``month`` — monthly OPEX day."""
    cal = calendar.Calendar()
    fridays = [d for d in cal.itermonthdates(year, month) if d.month == month and d.weekday() == 4]
    return fridays[2]


def opex_within_window(asof: date_type, days: int) -> date_type | None:
    """Return the next monthly OPEX date if it lands within ``days``, else None.

    Checks both the current month's third-Friday and (if the current month's
    has already passed) next month's so the window can roll over.
    """
    horizon = asof + timedelta(days=days)
    candidates = [third_friday(asof.year, asof.month)]
    nxt_year, nxt_month = (asof.year + (asof.month // 12), (asof.month % 12) + 1)
    candidates.append(third_friday(nxt_year, nxt_month))
    for d in candidates:
        if asof <= d <= horizon:
            return d
    return None


# -----------------------------------------------------------------------------
# Aggregate
# -----------------------------------------------------------------------------


def evaluate_schedule(
    conn: sqlite3.Connection,
    *,
    asof: date_type,
    candidate_tickers: Iterable[str],
    earnings_days: int = 2,
    fomc_days: int = 5,
    opex_days: int = 3,
) -> ScheduleAdvisories:
    """Run the PORT-05 schedule checks and return advisory warnings.

    Defaults:
      earnings_days = 2 (PORT-05 / SC4)
      fomc_days     = 5 (PORT-05 / SC4)
      opex_days     = 3 (PORT-05 / SC4)
    """
    items: list[Advisory] = []

    earn = find_earnings_within_window(
        conn, tickers=candidate_tickers, asof=asof, days=earnings_days
    )
    for ticker, expected in earn:
        items.append(
            Advisory(
                code="earnings_within_2d",
                message=f"{ticker} reports on {expected} (within {earnings_days}d)",
                severity="warn",
            )
        )

    fomc = find_fomc_within_window(conn, asof=asof, days=fomc_days)
    for d in fomc:
        items.append(
            Advisory(
                code="fomc_within_5d",
                message=f"FOMC meeting on {d.isoformat()} (within {fomc_days}d)",
                severity="warn",
            )
        )

    opex = opex_within_window(asof, opex_days)
    if opex is not None:
        items.append(
            Advisory(
                code="opex_within_3d",
                message=f"Monthly OPEX on {opex.isoformat()} (within {opex_days}d)",
                severity="warn",
            )
        )

    return ScheduleAdvisories(asof=asof, items=items)


__all__ = [
    "Advisory",
    "ScheduleAdvisories",
    "evaluate_schedule",
    "find_earnings_within_window",
    "find_fomc_within_window",
    "opex_within_window",
    "third_friday",
]
