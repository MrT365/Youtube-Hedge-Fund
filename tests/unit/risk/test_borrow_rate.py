"""Borrow-rate tracker tests."""

from __future__ import annotations

import pytest

from ls_equity_fund.risk.borrow_rate import (
    BorrowRateSnapshot,
    borrow_cost_bps_per_day,
    daily_borrow_cost_usd,
)


def test_hard_to_borrow_flag() -> None:
    assert BorrowRateSnapshot("XYZ", 0.10).hard_to_borrow is True
    assert BorrowRateSnapshot("XYZ", 0.099).hard_to_borrow is False


def test_do_not_short_flag() -> None:
    assert BorrowRateSnapshot("XYZ", 0.25).do_not_short is True
    assert BorrowRateSnapshot("XYZ", 0.01, available_shares=0).do_not_short is True


def test_daily_borrow_cost_feeds_short_pnl() -> None:
    assert daily_borrow_cost_usd(short_market_value=100_000, annualized_rate=0.365) == pytest.approx(100.0)
    assert borrow_cost_bps_per_day(0.365) == pytest.approx(10.0)
