from __future__ import annotations

import sqlite3

import pandas as pd

from ls_equity_fund.config import ExecutionConfig, PortfolioConfig
from ls_equity_fund.execution.order_executor import OrderExecutor, plan_chunks
from ls_equity_fund.execution.paper_broker import PaperBroker
from ls_equity_fund.risk.pre_trade_veto import VetoContext


class BorrowPaperBroker(PaperBroker):
    def check_short_availability(self, ticker: str) -> dict[str, object]:
        return {"available": True, "rate_pct": 5.0, "source": "ibkr_mock"}


def _approvals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "side": "long",
                "final_shares": 100.0,
                "limit_price": 100.0,
                "signal_price": 100.0,
                "adv_usd": 2_000_000.0,
                "sector": "Tech",
                "beta": 0.0,
            },
            {
                "ticker": "BBB",
                "side": "short",
                "final_shares": -100.0,
                "limit_price": 50.0,
                "signal_price": 50.0,
                "adv_usd": 2_000_000.0,
                "sector": "Health",
                "beta": 0.0,
            },
        ]
    )


def test_adv_chunking_rules() -> None:
    cfg = ExecutionConfig()
    small = plan_chunks(
        ticker="A",
        side="BUY",
        shares=100,
        limit_price=100,
        signal_price=100,
        adv_usd=2_000_000,
        is_closing_trade=False,
        cfg=cfg,
    )
    assert len(small) == 1
    capped = plan_chunks(
        ticker="B",
        side="BUY",
        shares=900,
        limit_price=100,
        signal_price=100,
        adv_usd=2_000_000,
        is_closing_trade=False,
        cfg=cfg,
    )
    assert 1 < len(capped) <= 5
    deferred = plan_chunks(
        ticker="C",
        side="BUY",
        shares=1_200,
        limit_price=100,
        signal_price=100,
        adv_usd=2_000_000,
        is_closing_trade=False,
        cfg=cfg,
    )
    assert deferred[0].deferred_reason == "order_gt_5pct_adv_deferred"


def test_execute_routes_veto_borrow_chunking_and_records_orders(conn: sqlite3.Connection) -> None:
    broker = BorrowPaperBroker()
    executor = OrderExecutor(
        broker=broker,
        conn=conn,
        execution_cfg=ExecutionConfig(),
        portfolio_cfg=PortfolioConfig(net_target_low=-1.0),
    )
    plans = executor.build_plan(
        _approvals(),
        context=VetoContext(aum_usd=1_000_000, max_net_beta=10.0),
    )
    assert plans
    assert max(p.chunk_total for p in plans if p.deferred_reason is None) <= 5
    assert any(p.borrow is not None for p in plans)

    executor.execute_plan(plans, run_id="exec-1", dry_run=False)
    rows = conn.execute(
        """
        SELECT ticker, side, signal_price, broker_order_id, status, chunk_total
        FROM orders
        ORDER BY id
        """
    ).fetchall()
    assert rows
    assert all(row[2] is not None for row in rows)
    assert all(row[3] for row in rows)
    assert all(row[4] == "FILLED" for row in rows)
    assert max(row[5] for row in rows) <= 5
