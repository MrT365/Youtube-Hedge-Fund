"""IBKR-native short availability and borrow-rate checks (EXEC-05 / EXEC-06)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class BorrowCheck:
    ticker: str
    available: bool
    rate_pct: float
    is_htb: bool
    source: str = "ibkr"
    reason: str | None = None


class ShortLocator:
    def __init__(self, broker: Any, *, max_borrow_rate_pct: float = 25.0, htb_rate_pct: float = 10.0) -> None:
        self._broker = broker
        self._max_borrow_rate_pct = max_borrow_rate_pct
        self._htb_rate_pct = htb_rate_pct

    def check(self, ticker: str) -> BorrowCheck:
        checker = getattr(self._broker, "check_short_availability", None)
        if callable(checker):
            raw = checker(ticker)
            available = bool(raw.get("available", True))
            rate_pct = float(raw.get("rate_pct", 0.0))
            source = str(raw.get("source", "ibkr"))
        else:
            available = True
            rate_pct = 0.0
            source = "paper_ibkr_mock"
        is_htb = rate_pct >= self._htb_rate_pct
        if not available:
            return BorrowCheck(ticker, False, rate_pct, is_htb, source, "no_ibkr_borrow_available")
        if rate_pct > self._max_borrow_rate_pct:
            return BorrowCheck(ticker, False, rate_pct, is_htb, source, "borrow_rate_gt_threshold")
        return BorrowCheck(ticker, True, rate_pct, is_htb, source)

    def persist(self, conn: sqlite3.Connection, check: BorrowCheck, *, as_of_date: date | None = None) -> None:
        with conn:
            conn.execute(
                """
                INSERT INTO borrow_rates (ticker, rate_pct, is_htb, as_of_date, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    check.ticker,
                    check.rate_pct,
                    int(check.is_htb),
                    (as_of_date or date.today()).isoformat(),
                    check.source,
                ),
            )

    def poll_open_shorts(self, conn: sqlite3.Connection, tickers: list[str]) -> list[BorrowCheck]:
        checks = [self.check(t) for t in tickers]
        for check in checks:
            self.persist(conn, check)
        return checks


__all__ = ["BorrowCheck", "ShortLocator"]
