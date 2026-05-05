"""Turnover and configurable tax analytics (REPORT-05)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ls_equity_fund.config import TaxConfig


@dataclass(frozen=True)
class TurnoverSummary:
    turnover_30d: float
    turnover_90d: float
    annualized_turnover: float
    budget: float
    tax_estimate: float
    jurisdiction_name: str


def turnover_rate(trades: pd.DataFrame, *, days: int, aum_usd: float) -> float:
    if trades.empty or aum_usd <= 0:
        return 0.0
    df = trades.copy()
    df["date"] = pd.to_datetime(df["date"])
    cutoff = df["date"].max() - pd.Timedelta(days=days)
    notional = (df[df["date"] >= cutoff]["shares"].abs() * df[df["date"] >= cutoff]["price"]).sum()
    return float(notional / aum_usd)


def tax_estimate(round_trips: pd.DataFrame, tax: TaxConfig) -> float:
    if round_trips.empty:
        return 0.0
    winners = round_trips[round_trips["realized_pnl"] > 0].copy()
    if winners.empty:
        return 0.0
    rates = winners["holding_days"].map(lambda d: tax.short_term_rate if int(d) < 365 else tax.long_term_rate)
    return float((winners["realized_pnl"] * rates).sum())


def summarize_turnover(
    trades: pd.DataFrame,
    round_trips: pd.DataFrame,
    *,
    aum_usd: float,
    budget: float,
    tax: TaxConfig,
) -> TurnoverSummary:
    t30 = turnover_rate(trades, days=30, aum_usd=aum_usd)
    t90 = turnover_rate(trades, days=90, aum_usd=aum_usd)
    return TurnoverSummary(
        turnover_30d=t30,
        turnover_90d=t90,
        annualized_turnover=t30 * 365 / 30,
        budget=budget,
        tax_estimate=tax_estimate(round_trips, tax),
        jurisdiction_name=tax.jurisdiction_name,
    )


__all__ = ["TurnoverSummary", "summarize_turnover", "tax_estimate", "turnover_rate"]
