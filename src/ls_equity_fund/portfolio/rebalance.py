"""Rebalance generator (PORT-09).

Diff target book vs current book → ordered list of trades. Honours:

  * 30% turnover budget (or ``cfg.turnover_budget``) — defined as
    ``sum(|delta_dollar|) / target_aum_usd``. If the raw diff exceeds budget,
    we keep the highest-priority trades and drop the smallest ones.
  * Priority = ``|score_change|`` (largest score swings first), with new
    additions prioritised over weight tweaks.
  * Per-trade transaction-cost decomposition (commission + spread + impact)
    via :func:`portfolio.transaction_cost.estimate_trade_cost`.

The output is a DataFrame keyed by ``(ticker, side)`` with one row per
proposed action. ``action`` ∈ {"open", "close", "increase", "reduce", "flip"}
captures what the trade does.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.transaction_cost import TradeCost, estimate_trade_cost


@dataclass(frozen=True)
class RebalanceSummary:
    """Aggregate rebalance summary."""

    n_trades: int
    raw_turnover: float
    final_turnover: float
    total_trade_value: float
    total_cost_usd: float
    dropped_for_budget: int


def _classify_action(prev_shares: float, new_shares: float) -> str:
    if abs(prev_shares) < 1e-9 and abs(new_shares) < 1e-9:
        return "noop"
    if abs(prev_shares) < 1e-9:
        return "open"
    if abs(new_shares) < 1e-9:
        return "close"
    if np.sign(prev_shares) != np.sign(new_shares):
        return "flip"
    if abs(new_shares) > abs(prev_shares):
        return "increase"
    return "reduce"


def generate_rebalance(
    *,
    targets: pd.DataFrame,
    current: pd.DataFrame,
    cfg: PortfolioConfig,
    target_aum_usd: float | None = None,
    prev_scores: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, RebalanceSummary]:
    """Compute rebalance trades + per-trade costs.

    Args:
        targets: from :func:`build_target_book` — must contain ticker, side,
            final_weight, final_shares, target_dollar, limit_price, sector,
            adv_usd, score (combined percentile).
        current: from :func:`load_current_positions` — must contain ticker,
            side, shares, current_price, sector. May be empty.
        cfg: Portfolio config (turnover budget, transaction cost model).
        target_aum_usd: total AUM the rebalance is sized against. Defaults
            to ``cfg.target_aum_usd``.
        prev_scores: optional ticker→prev-score map; used to prioritise by
            ``|score_change|``. Falls back to ``|target_score|`` when missing.

    Returns:
        (trades_df, summary) — trades_df rows are sorted by priority desc,
        with a ``dropped_for_budget`` flag on rows that were trimmed.
    """
    target_aum_usd = target_aum_usd or cfg.target_aum_usd
    prev_scores = prev_scores or {}

    # Build a lookup on (ticker, side) for both books.
    tgt = (
        targets.copy()
        if not targets.empty
        else pd.DataFrame(
            columns=[
                "ticker",
                "side",
                "final_weight",
                "final_shares",
                "target_dollar",
                "limit_price",
                "sector",
                "adv_usd",
                "score",
            ]
        )
    )
    cur = (
        current.copy()
        if not current.empty
        else pd.DataFrame(columns=["ticker", "side", "shares", "current_price", "sector"])
    )

    if not tgt.empty:
        tgt = tgt.rename(
            columns={
                "final_shares": "target_shares",
                "target_dollar": "target_dollar",
                "limit_price": "target_price",
            }
        )
    if not cur.empty:
        cur = cur.rename(
            columns={
                "shares": "current_shares",
                "current_price": "current_price",
            }
        )

    keys: set[tuple[str, str]] = set()
    if not tgt.empty:
        keys.update(zip(tgt["ticker"], tgt["side"], strict=False))
    if not cur.empty:
        keys.update(zip(cur["ticker"], cur["side"], strict=False))

    rows: list[dict[str, object]] = []
    for ticker, side in sorted(keys):
        tgt_row = (
            tgt[(tgt["ticker"] == ticker) & (tgt["side"] == side)].iloc[0].to_dict()
            if not tgt.empty and ((tgt["ticker"] == ticker) & (tgt["side"] == side)).any()
            else {}
        )
        cur_row = (
            cur[(cur["ticker"] == ticker) & (cur["side"] == side)].iloc[0].to_dict()
            if not cur.empty and ((cur["ticker"] == ticker) & (cur["side"] == side)).any()
            else {}
        )

        target_shares = float(tgt_row.get("target_shares", 0.0) or 0.0)
        current_shares = float(cur_row.get("current_shares", 0.0) or 0.0)
        delta_shares = target_shares - current_shares
        if abs(delta_shares) < 1e-9:
            continue

        target_price_raw = tgt_row.get("target_price")
        if target_price_raw is not None and not pd.isna(target_price_raw):
            price = float(target_price_raw)
        else:
            price = float(cur_row.get("current_price") or 0.0)
        adv_usd = float(tgt_row.get("adv_usd") or 0.0)
        sector = tgt_row.get("sector") or cur_row.get("sector")
        score = tgt_row.get("score")

        trade_value = abs(delta_shares) * price
        is_sell = (
            (side == "long" and delta_shares < 0)  # selling longs
            or (side == "short" and delta_shares > 0)  # covering shorts (buy to cover)
            # Note: SEC fee technically also charges on long sells; covering is
            # a buy. We over-flag here for v1; Phase 8 will refine.
        )
        cost: TradeCost = estimate_trade_cost(
            shares=delta_shares,
            price=price,
            adv_usd=adv_usd,
            cfg=cfg.transaction_cost,
            is_sell=is_sell,
        )

        action = _classify_action(current_shares, target_shares)

        prev_score = prev_scores.get(ticker)
        target_score = score if score is not None and not pd.isna(score) else None
        if prev_score is not None and target_score is not None:
            score_change = abs(float(target_score) - float(prev_score))
        elif target_score is not None:
            score_change = float(target_score) if action == "open" else 0.0
        else:
            score_change = 0.0

        priority = score_change + (50.0 if action == "open" else 0.0)
        rows.append(
            {
                "ticker": ticker,
                "side": side,
                "action": action,
                "current_shares": current_shares,
                "target_shares": target_shares,
                "delta_shares": delta_shares,
                "price": price,
                "trade_value": trade_value,
                "sector": sector,
                "score": target_score,
                "score_change": score_change,
                "priority": priority,
                "commission_usd": cost.commission_usd,
                "spread_usd": cost.spread_usd,
                "impact_usd": cost.impact_usd,
                "sec_fee_usd": cost.sec_fee_usd,
                "total_cost_usd": cost.total_usd,
                "commission_bps": cost.commission_bps,
                "spread_bps": cost.spread_bps,
                "impact_bps": cost.impact_bps,
                "total_cost_bps": cost.total_bps,
                "dropped_for_budget": False,
            }
        )

    if not rows:
        return pd.DataFrame(rows), RebalanceSummary(
            n_trades=0,
            raw_turnover=0.0,
            final_turnover=0.0,
            total_trade_value=0.0,
            total_cost_usd=0.0,
            dropped_for_budget=0,
        )

    trades = (
        pd.DataFrame(rows)
        .sort_values(["priority", "trade_value"], ascending=[False, False])
        .reset_index(drop=True)
    )

    raw_turnover = float(trades["trade_value"].sum() / max(target_aum_usd, 1e-9))
    budget_usd = cfg.turnover_budget * target_aum_usd

    # Greedy keep highest priority trades within budget.
    cum = trades["trade_value"].cumsum()
    keep_mask = cum <= budget_usd
    if not keep_mask.all():
        # Allow the trade that bumps the cum exactly over the line if its
        # delta is small enough to absorb the remaining budget.
        first_drop = (~keep_mask).idxmax()
        trades.loc[first_drop:, "dropped_for_budget"] = True
        # The first dropped trade is allowed if cum-prev <= budget (we already
        # crossed; just skip).
    final_turnover = float(
        trades.loc[~trades["dropped_for_budget"], "trade_value"].sum() / max(target_aum_usd, 1e-9)
    )

    summary = RebalanceSummary(
        n_trades=int((~trades["dropped_for_budget"]).sum()),
        raw_turnover=raw_turnover,
        final_turnover=final_turnover,
        total_trade_value=float(trades.loc[~trades["dropped_for_budget"], "trade_value"].sum()),
        total_cost_usd=float(trades.loc[~trades["dropped_for_budget"], "total_cost_usd"].sum()),
        dropped_for_budget=int(trades["dropped_for_budget"].sum()),
    )
    return trades, summary


__all__ = ["RebalanceSummary", "generate_rebalance"]
