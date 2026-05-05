"""Short borrow cost tracker (Phase 6 gap G8)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BorrowRateSnapshot:
    ticker: str
    annualized_rate: float
    available_shares: float | None = None

    @property
    def hard_to_borrow(self) -> bool:
        return self.annualized_rate >= 0.10

    @property
    def do_not_short(self) -> bool:
        return self.annualized_rate >= 0.25 or self.available_shares == 0


def annual_borrow_cost_usd(*, short_market_value: float, annualized_rate: float) -> float:
    """Annual borrow drag for a short position."""
    return abs(short_market_value) * max(annualized_rate, 0.0)


def daily_borrow_cost_usd(*, short_market_value: float, annualized_rate: float) -> float:
    return annual_borrow_cost_usd(
        short_market_value=short_market_value,
        annualized_rate=annualized_rate,
    ) / 365.0


def borrow_cost_bps_per_day(annualized_rate: float) -> float:
    """Daily borrow cost in basis points of notional."""
    return max(annualized_rate, 0.0) / 365.0 * 10_000.0


__all__ = [
    "BorrowRateSnapshot",
    "annual_borrow_cost_usd",
    "borrow_cost_bps_per_day",
    "daily_borrow_cost_usd",
]
