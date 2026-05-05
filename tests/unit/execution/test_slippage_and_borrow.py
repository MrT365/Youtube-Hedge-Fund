from __future__ import annotations

import sqlite3

import pytest

from ls_equity_fund.config import TransactionCostConfig
from ls_equity_fund.execution.short_locator import ShortLocator
from ls_equity_fund.execution.slippage_tracker import record_slippage, rolling_stats, slippage_bps
from ls_equity_fund.portfolio.transaction_cost import estimate_trade_cost


class BorrowBroker:
    def check_short_availability(self, ticker: str) -> dict[str, object]:
        return {"available": True, "rate_pct": 12.5, "source": "ibkr_mock"}


def test_slippage_bps_is_side_aware() -> None:
    assert slippage_bps(side="BUY", signal_price=100, fill_price=101) == pytest.approx(100.0)
    assert slippage_bps(side="SELL_SHORT", signal_price=100, fill_price=99) == pytest.approx(100.0)
    assert slippage_bps(side="SELL", signal_price=100, fill_price=101) == pytest.approx(-100.0)


def test_rolling_slippage_stats(conn: sqlite3.Connection) -> None:
    now = 2_000_000
    for i, bps in enumerate([10, 20, 30, 40, 50, 100]):
        signal = 100.0
        fill = signal * (1 + bps / 10_000)
        record_slippage(
            conn,
            run_id="r1",
            ticker=f"T{i}",
            side="BUY",
            signal_price=signal,
            fill_price=fill,
            timestamp=now - i,
        )
    old_fill = 100 * (1 + 500 / 10_000)
    record_slippage(
        conn,
        run_id="old",
        ticker="OLD",
        side="BUY",
        signal_price=100,
        fill_price=old_fill,
        timestamp=now - 31 * 86_400,
    )

    stats = rolling_stats(conn, now_ts=now)
    assert stats.avg_bps == pytest.approx(41.6666667)
    assert stats.median_bps == pytest.approx(35.0)
    assert stats.p95_bps == pytest.approx(87.5)
    assert list(stats.worst_5["ticker"]) == ["T5", "T4", "T3", "T2", "T1"]


def test_htb_borrow_rate_persisted_and_feeds_transaction_cost(conn: sqlite3.Connection) -> None:
    locator = ShortLocator(BorrowBroker(), max_borrow_rate_pct=25.0, htb_rate_pct=10.0)
    check = locator.check("HTB")
    locator.persist(conn, check)
    row = conn.execute("SELECT ticker, rate_pct, is_htb, source FROM borrow_rates").fetchone()
    assert row == ("HTB", 12.5, 1, "ibkr_mock")

    cost = estimate_trade_cost(
        shares=100,
        price=100,
        adv_usd=1_000_000,
        cfg=TransactionCostConfig(),
        is_sell=True,
        borrow_rate_pct=check.rate_pct,
    )
    assert cost.borrow_bps > 0
    assert cost.total_bps > cost.commission_bps + cost.spread_bps + cost.impact_bps
