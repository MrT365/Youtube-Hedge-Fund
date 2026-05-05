"""Rebalance generator tests (PORT-09)."""

from __future__ import annotations

import pandas as pd

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.rebalance import generate_rebalance


def _cfg(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "num_longs": 5,
        "num_shorts": 5,
        "turnover_budget": 0.30,
        "target_aum_usd": 1_000_000.0,
    }
    base.update(overrides)
    return PortfolioConfig(**base)


def _target_row(ticker: str, side: str, shares: float, price: float, score: float = 75.0) -> dict:
    return {
        "ticker": ticker,
        "side": side,
        "final_weight": 0.05,
        "final_shares": shares,
        "target_dollar": shares * price,
        "limit_price": price,
        "sector": "Tech",
        "adv_usd": 100_000_000.0,
        "score": score,
    }


def test_generate_rebalance_opens_new_positions() -> None:
    targets = pd.DataFrame([_target_row("AAA", "long", 100, 50.0)])
    current = pd.DataFrame(columns=["ticker", "side", "shares", "current_price", "sector"])
    cfg = _cfg()
    trades, summary = generate_rebalance(
        targets=targets,
        current=current,
        cfg=cfg,
        target_aum_usd=1_000_000,
    )
    assert len(trades) == 1
    assert trades.iloc[0]["action"] == "open"
    assert trades.iloc[0]["delta_shares"] == 100
    assert summary.n_trades == 1


def test_generate_rebalance_closes_old_positions() -> None:
    targets = pd.DataFrame(
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
    current = pd.DataFrame(
        [
            {
                "ticker": "OLD",
                "side": "long",
                "shares": 100,
                "current_price": 60.0,
                "sector": "Tech",
            }
        ]
    )
    cfg = _cfg()
    trades, _ = generate_rebalance(
        targets=targets,
        current=current,
        cfg=cfg,
        target_aum_usd=1_000_000,
    )
    assert len(trades) == 1
    assert trades.iloc[0]["action"] == "close"
    assert trades.iloc[0]["delta_shares"] == -100


def test_turnover_budget_drops_low_priority_trades() -> None:
    """Build $1M of trades but cap turnover at 10% → most should drop."""
    rows = [_target_row(f"T{i:02d}", "long", 100, 100.0, score=50.0 + i) for i in range(20)]
    targets = pd.DataFrame(rows)
    current = pd.DataFrame(columns=["ticker", "side", "shares", "current_price", "sector"])
    cfg = _cfg(turnover_budget=0.10)  # $100k budget
    _trades, summary = generate_rebalance(
        targets=targets,
        current=current,
        cfg=cfg,
        target_aum_usd=1_000_000,
    )
    # Each trade is $10k; budget $100k → keep 10, drop 10.
    assert summary.dropped_for_budget >= 9
    assert summary.final_turnover <= 0.10 + 1e-6


def test_priority_orders_by_score_change_when_provided() -> None:
    targets = pd.DataFrame(
        [
            _target_row("HIGH", "long", 100, 50.0, score=95.0),
            _target_row("LOW", "long", 100, 50.0, score=70.0),
        ]
    )
    current = pd.DataFrame(
        [
            {
                "ticker": "HIGH",
                "side": "long",
                "shares": 50,
                "current_price": 50.0,
                "sector": "Tech",
            },
            {
                "ticker": "LOW",
                "side": "long",
                "shares": 50,
                "current_price": 50.0,
                "sector": "Tech",
            },
        ]
    )
    cfg = _cfg(turnover_budget=1.0)
    trades, _ = generate_rebalance(
        targets=targets,
        current=current,
        cfg=cfg,
        target_aum_usd=1_000_000,
        prev_scores={"HIGH": 60.0, "LOW": 68.0},
    )
    # HIGH had bigger score swing (60→95 = 35) vs LOW (68→70 = 2)
    assert trades.iloc[0]["ticker"] == "HIGH"


def test_costs_decompose_per_trade() -> None:
    targets = pd.DataFrame([_target_row("AAA", "long", 1000, 100.0)])
    current = pd.DataFrame(columns=["ticker", "side", "shares", "current_price", "sector"])
    cfg = _cfg()
    trades, _ = generate_rebalance(
        targets=targets,
        current=current,
        cfg=cfg,
        target_aum_usd=1_000_000,
    )
    row = trades.iloc[0]
    assert row["commission_usd"] > 0  # IBKR tiered baseline
    assert row["spread_usd"] > 0
    assert row["impact_usd"] > 0
    assert row["total_cost_usd"] >= row["commission_usd"] + row["spread_usd"]
