"""Absolute pre-trade veto layer (RISK-03, RISK-04, RISK-05).

Any failed check rejects the trade and persists an immutable audit row to
``veto_log``. The only exemption is the explicit CP5 closing-trade rule:
sign-preserving, magnitude-reducing, and trade quantity no larger than the
existing position.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ls_equity_fund.config import PortfolioConfig

EPS = 1e-9


@dataclass(frozen=True)
class TradeRequest:
    ticker: str
    side: str
    shares: float
    price: float
    sector: str | None = None
    beta: float | None = None
    adv_20d_usd: float | None = None
    is_new_entry: bool = False
    claimed_closing: bool = False


@dataclass(frozen=True)
class VetoContext:
    aum_usd: float
    current_positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    halted_tickers: set[str] = field(default_factory=set)
    earnings_dates: dict[str, date] = field(default_factory=dict)
    betas: dict[str, float] = field(default_factory=dict)
    correlations: pd.DataFrame = field(default_factory=pd.DataFrame)
    asof: date | None = None
    max_net_beta: float = 0.20
    max_pairwise_corr: float = 0.80
    earnings_blackout_days: int = 2


@dataclass(frozen=True)
class VetoResult:
    accepted: bool
    reasons: list[str]
    is_closing_trade: bool


def is_closing_trade(existing_shares: float, trade_shares: float) -> bool:
    """CP5 closing-trade definition.

    A closing trade must be sign-preserving after the trade, magnitude-reducing,
    and the trade quantity cannot exceed the existing share count. A label from
    upstream is ignored; this predicate is the source of truth.
    """
    if abs(existing_shares) <= EPS or abs(trade_shares) <= EPS:
        return False
    new_position = existing_shares + trade_shares
    if abs(new_position) <= EPS:
        return False
    return bool(
        np.sign(new_position) == np.sign(existing_shares)
        and abs(new_position) < abs(existing_shares)
        and abs(trade_shares) <= abs(existing_shares)
    )


def evaluate_pre_trade_veto(
    conn: sqlite3.Connection,
    *,
    trade: TradeRequest,
    context: VetoContext,
    portfolio_cfg: PortfolioConfig,
    persist: bool = True,
) -> VetoResult:
    """Run all eight absolute checks and persist any rejection."""
    asof = context.asof or date.today()
    existing_shares = _existing_signed_shares(context.current_positions, trade.ticker)
    closing = is_closing_trade(existing_shares, trade.shares)
    trade_context = _trade_context(trade, context, existing_shares, closing)
    reasons: list[str] = []

    if trade.ticker in context.halted_tickers:
        reasons.append("halt_lock")

    if not closing and _is_earnings_blackout(trade.ticker, asof, context):
        reasons.append("earnings_blackout")

    trade_value = abs(trade.shares) * trade.price
    adv_20d_usd = trade.adv_20d_usd if trade.adv_20d_usd is not None else _load_adv_20d(conn, trade.ticker, asof)
    if adv_20d_usd is not None and adv_20d_usd > 0 and trade_value > 0.05 * adv_20d_usd:
        reasons.append("liquidity_gt_5pct_adv")

    new_position_value = abs(existing_shares + trade.shares) * trade.price
    if context.aum_usd > 0 and new_position_value > portfolio_cfg.max_position_pct * context.aum_usd:
        reasons.append("position_size_gt_5pct_aum")

    if _sector_exposure_after(trade, context) > portfolio_cfg.max_sector_pct:
        reasons.append("sector_concentration_gt_25pct")

    gross, net = _gross_net_after(trade, context)
    if gross > portfolio_cfg.gross_target + 1e-6 or not (
        portfolio_cfg.net_target_low - 1e-6 <= net <= portfolio_cfg.net_target_high + 1e-6
    ):
        reasons.append("gross_net_exposure_out_of_bounds")

    net_beta = _net_beta_after(trade, context)
    if abs(net_beta) > context.max_net_beta:
        reasons.append("net_beta_gt_0.20")

    if _max_pairwise_corr(trade.ticker, context) > context.max_pairwise_corr:
        reasons.append("pairwise_correlation_gt_0.80")

    if reasons and persist:
        for reason in reasons:
            write_veto_log(conn, trade=trade, reason=reason, trade_context=trade_context)
    return VetoResult(accepted=not reasons, reasons=reasons, is_closing_trade=closing)


def write_veto_log(
    conn: sqlite3.Connection,
    *,
    trade: TradeRequest,
    reason: str,
    trade_context: dict[str, Any],
) -> None:
    """Persist one immutable veto rejection row."""
    with conn:
        conn.execute(
            """
            INSERT INTO veto_log (
                timestamp, ticker, side, shares, reason, trade_context_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                trade.ticker,
                trade.side,
                float(trade.shares),
                reason,
                json.dumps(trade_context, sort_keys=True, default=str),
            ),
        )


def load_recent_vetoes(conn: sqlite3.Connection, *, limit: int = 20) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT timestamp, ticker, side, shares, reason, trade_context_json
        FROM veto_log
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=[limit],
    )


def _trade_context(
    trade: TradeRequest,
    context: VetoContext,
    existing_shares: float,
    closing: bool,
) -> dict[str, Any]:
    return {
        "trade": asdict(trade),
        "existing_shares": existing_shares,
        "computed_is_closing_trade": closing,
        "claimed_closing_ignored": trade.claimed_closing,
        "aum_usd": context.aum_usd,
    }


def _existing_signed_shares(current_positions: pd.DataFrame, ticker: str) -> float:
    if current_positions.empty or "ticker" not in current_positions.columns:
        return 0.0
    rows = current_positions[current_positions["ticker"] == ticker]
    if rows.empty:
        return 0.0
    return float(rows["shares"].sum())


def _is_earnings_blackout(ticker: str, asof: date, context: VetoContext) -> bool:
    earnings_date = context.earnings_dates.get(ticker)
    return earnings_date is not None and 0 <= (earnings_date - asof).days <= context.earnings_blackout_days


def _load_adv_20d(conn: sqlite3.Connection, ticker: str, asof: date) -> float | None:
    df = pd.read_sql_query(
        """
        SELECT close, volume
        FROM daily_prices
        WHERE ticker = ? AND date <= ?
        ORDER BY date DESC
        LIMIT 20
        """,
        conn,
        params=[ticker, asof.isoformat()],
    )
    if df.empty:
        return None
    adv = (pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")).mean()
    return float(adv) if not pd.isna(adv) else None


def _position_values(current_positions: pd.DataFrame) -> pd.Series:
    if current_positions.empty:
        return pd.Series(dtype=float)
    px = current_positions["current_price"].fillna(current_positions["entry_price"]).astype(float)
    return current_positions["shares"].astype(float) * px


def _sector_exposure_after(trade: TradeRequest, context: VetoContext) -> float:
    if context.aum_usd <= 0 or trade.sector is None:
        return 0.0
    positions = context.current_positions.copy()
    sector_value = 0.0
    if not positions.empty and "sector" in positions.columns:
        values = _position_values(positions).abs()
        sector_value = float(values[positions["sector"] == trade.sector].sum())
    sector_value += abs(trade.shares) * trade.price
    return sector_value / context.aum_usd


def _gross_net_after(trade: TradeRequest, context: VetoContext) -> tuple[float, float]:
    if context.aum_usd <= 0:
        return 0.0, 0.0
    values = _position_values(context.current_positions)
    gross = float(values.abs().sum())
    net = float(values.sum())
    trade_value = trade.shares * trade.price
    gross += abs(trade_value)
    net += trade_value
    return gross / context.aum_usd, net / context.aum_usd


def _net_beta_after(trade: TradeRequest, context: VetoContext) -> float:
    if context.aum_usd <= 0:
        return 0.0
    values = _position_values(context.current_positions)
    net_beta = 0.0
    if not context.current_positions.empty:
        for ticker, value in zip(context.current_positions["ticker"], values, strict=False):
            net_beta += (float(value) / context.aum_usd) * float(context.betas.get(ticker, 0.0))
    beta = trade.beta if trade.beta is not None else context.betas.get(trade.ticker, 0.0)
    net_beta += (trade.shares * trade.price / context.aum_usd) * float(beta or 0.0)
    return net_beta


def _max_pairwise_corr(ticker: str, context: VetoContext) -> float:
    corr = context.correlations
    if corr.empty or ticker not in corr.index:
        return 0.0
    existing = (
        context.current_positions["ticker"].tolist()
        if not context.current_positions.empty and "ticker" in context.current_positions.columns
        else []
    )
    vals = [abs(float(corr.loc[ticker, t])) for t in existing if t in corr.columns and t != ticker]
    return max(vals) if vals else 0.0


__all__ = [
    "TradeRequest",
    "VetoContext",
    "VetoResult",
    "evaluate_pre_trade_veto",
    "is_closing_trade",
    "load_recent_vetoes",
    "write_veto_log",
]
