"""Order planning and execution (EXEC-03)."""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from ls_equity_fund.config import ExecutionConfig, PortfolioConfig
from ls_equity_fund.execution.short_locator import BorrowCheck, ShortLocator
from ls_equity_fund.execution.slippage_tracker import record_slippage
from ls_equity_fund.risk.pre_trade_veto import (
    TradeRequest,
    VetoContext,
    evaluate_pre_trade_veto,
)
from ls_equity_fund.schemas import Order, OrderId, OrderStatus, Side


@dataclass(frozen=True)
class OrderPlan:
    ticker: str
    side: str
    shares: float
    limit_price: float
    signal_price: float
    tif: str
    chunk_index: int
    chunk_total: int
    adv_usd: float
    is_closing_trade: bool
    deferred_reason: str | None = None
    borrow: BorrowCheck | None = None


def plan_chunks(
    *,
    ticker: str,
    side: str,
    shares: float,
    limit_price: float,
    signal_price: float,
    adv_usd: float,
    is_closing_trade: bool,
    cfg: ExecutionConfig,
    borrow: BorrowCheck | None = None,
) -> list[OrderPlan]:
    notional = abs(shares) * signal_price
    participation = notional / adv_usd if adv_usd > 0 else 0.0
    if participation > cfg.chunk_defer_adv_pct:
        return [
            OrderPlan(
                ticker=ticker,
                side=side,
                shares=shares,
                limit_price=limit_price,
                signal_price=signal_price,
                tif=cfg.tif,
                chunk_index=0,
                chunk_total=0,
                adv_usd=adv_usd,
                is_closing_trade=is_closing_trade,
                deferred_reason="order_gt_5pct_adv_deferred",
                borrow=borrow,
            )
        ]
    if participation < cfg.chunk_skip_adv_pct or adv_usd <= 0:
        chunk_total = 1
    else:
        chunk_total = min(cfg.max_chunks, max(1, math.ceil(participation / cfg.chunk_skip_adv_pct)))
    abs_shares = abs(shares)
    chunk_abs = abs_shares / chunk_total
    sign = 1.0 if shares > 0 else -1.0
    return [
        OrderPlan(
            ticker=ticker,
            side=side,
            shares=sign * chunk_abs,
            limit_price=limit_price,
            signal_price=signal_price,
            tif=cfg.tif,
            chunk_index=i + 1,
            chunk_total=chunk_total,
            adv_usd=adv_usd,
            is_closing_trade=is_closing_trade,
            borrow=borrow,
        )
        for i in range(chunk_total)
    ]


class OrderExecutor:
    def __init__(
        self,
        *,
        broker: Any,
        conn: sqlite3.Connection,
        execution_cfg: ExecutionConfig,
        portfolio_cfg: PortfolioConfig,
        short_locator: ShortLocator | None = None,
    ) -> None:
        self.broker = broker
        self.conn = conn
        self.execution_cfg = execution_cfg
        self.portfolio_cfg = portfolio_cfg
        self.short_locator = short_locator or ShortLocator(
            broker,
            max_borrow_rate_pct=execution_cfg.borrow_rate_skip_pct,
            htb_rate_pct=execution_cfg.htb_rate_pct,
        )

    def build_plan(
        self,
        approvals: pd.DataFrame,
        *,
        context: VetoContext,
    ) -> list[OrderPlan]:
        plans: list[OrderPlan] = []
        for _, row in approvals.iterrows():
            signed_shares = _signed_shares(row)
            if abs(signed_shares) <= 0:
                continue
            side = _side_for(row, signed_shares)
            signal_price = _price_for(row, self.execution_cfg.limit_price_policy)
            trade = TradeRequest(
                ticker=str(row["ticker"]),
                side=side,
                shares=signed_shares,
                price=signal_price,
                sector=row.get("sector"),
                beta=row.get("beta"),
                adv_20d_usd=float(row.get("adv_usd") or 0.0),
            )
            veto = evaluate_pre_trade_veto(
                self.conn,
                trade=trade,
                context=context,
                portfolio_cfg=self.portfolio_cfg,
                persist=True,
            )
            if not veto.accepted:
                continue
            borrow = None
            if side == Side.SELL_SHORT.value and not veto.is_closing_trade:
                borrow = self.short_locator.check(str(row["ticker"]))
                self.short_locator.persist(self.conn, borrow)
                if not borrow.available:
                    continue
            plans.extend(
                plan_chunks(
                    ticker=str(row["ticker"]),
                    side=side,
                    shares=signed_shares,
                    limit_price=signal_price,
                    signal_price=signal_price,
                    adv_usd=float(row.get("adv_usd") or 0.0),
                    is_closing_trade=veto.is_closing_trade,
                    cfg=self.execution_cfg,
                    borrow=borrow,
                )
            )
        return plans

    def execute_plan(self, plans: list[OrderPlan], *, run_id: str, dry_run: bool) -> list[OrderPlan]:
        submitted: list[OrderPlan] = []
        if not dry_run:
            permission_check = getattr(self.broker, "ensure_realtime_market_data", None)
            if callable(permission_check):
                permission_check()
        for plan in plans:
            if plan.deferred_reason is not None:
                self.persist_order(plan, run_id=run_id, status="DEFERRED", broker_order_id="DEFERRED")
                continue
            if dry_run:
                submitted.append(plan)
                continue
            order = Order(
                order_id=OrderId(f"{run_id}-{plan.ticker}-{plan.chunk_index}"),
                ticker=plan.ticker,
                side=_schema_side(plan.side),
                qty=max(1, round(abs(plan.shares))),
                signal_price=plan.signal_price,
            )
            broker_order_id = self.broker.place_order(order)
            filled = self.broker.get_order(broker_order_id)
            status = filled.status.value if isinstance(filled.status, OrderStatus) else str(filled.status)
            fill_price = filled.fill_price
            slippage = None
            if fill_price is not None:
                slippage = record_slippage(
                    self.conn,
                    run_id=run_id,
                    ticker=plan.ticker,
                    side=plan.side,
                    signal_price=plan.signal_price,
                    fill_price=float(fill_price),
                )
            self.persist_order(
                plan,
                run_id=run_id,
                status=status,
                broker_order_id=str(broker_order_id),
                fill_price=fill_price,
                slippage_bps=slippage,
            )
            submitted.append(plan)
        return submitted

    def persist_order(
        self,
        plan: OrderPlan,
        *,
        run_id: str,
        status: str,
        broker_order_id: str,
        fill_price: float | None = None,
        slippage_bps: float | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO orders (
                    timestamp, ticker, side, shares, limit_price, fill_price,
                    slippage_bps, status, broker_order_id, signal_price,
                    is_closing_trade, run_id, tif, chunk_index, chunk_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    plan.ticker,
                    plan.side,
                    float(plan.shares),
                    plan.limit_price,
                    fill_price,
                    slippage_bps,
                    status,
                    broker_order_id,
                    plan.signal_price,
                    int(plan.is_closing_trade),
                    run_id,
                    plan.tif,
                    plan.chunk_index,
                    plan.chunk_total,
                ),
            )


def _signed_shares(row: pd.Series) -> float:
    shares = float(row.get("final_shares") or row.get("shares") or 0.0)
    side = str(row.get("side") or "").lower()
    if side == "short" and shares > 0:
        return -shares
    return shares


def _side_for(row: pd.Series, signed_shares: float) -> str:
    side = str(row.get("side") or "").lower()
    if signed_shares < 0:
        return Side.SELL_SHORT.value if side == "short" else Side.SELL.value
    return Side.BUY.value if side == "long" else Side.BUY_TO_COVER.value


def _price_for(row: pd.Series, policy: str) -> float:
    if policy == "close":
        return float(row.get("close") or row.get("limit_price") or row.get("signal_price") or 0.0)
    if policy == "market_reference":
        return float(row.get("market_reference") or row.get("limit_price") or row.get("signal_price") or 0.0)
    return float(row.get("signal_price") or row.get("limit_price") or row.get("price") or 0.0)


def _schema_side(side: str) -> Side:
    return Side(side)


def with_adv(approvals: pd.DataFrame, adv_by_ticker: dict[str, float]) -> pd.DataFrame:
    out = approvals.copy()
    out["adv_usd"] = out["ticker"].map(adv_by_ticker).fillna(out.get("adv_usd", 0.0))
    return out


def mark_deferred(plan: OrderPlan, reason: str) -> OrderPlan:
    return replace(plan, deferred_reason=reason)


__all__ = ["OrderExecutor", "OrderPlan", "mark_deferred", "plan_chunks", "with_adv"]
