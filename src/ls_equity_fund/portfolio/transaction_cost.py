"""Transaction-cost model (PORT-04).

Decomposes per-trade cost into three components:

  * commission_usd  — broker-configurable model (IBKR tiered / lite / fixed
    cents-per-share / zero) plus an optional SEC/TAF/FINRA pass-through. NOT
    hardcoded $0.
  * spread_bps      — half-spread cost charged on every fill regardless of
    direction. Constant from config (calibrated against Phase 8's slippage
    table once it exists; v1 default 4 bps).
  * impact_bps      — square-root market-impact: ``coef * sqrt(notional/adv)``.
    Empirical Almgren-Chriss-style scaler; coef from config.

Returns a dataclass with usd + bps decomposition so the rebalance generator
can show the operator commission / spread / impact breakdown per trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ls_equity_fund.config import TransactionCostConfig


@dataclass(frozen=True)
class TradeCost:
    """Per-trade cost decomposition (PORT-04)."""

    commission_usd: float
    spread_usd: float
    impact_usd: float
    sec_fee_usd: float

    commission_bps: float
    spread_bps: float
    impact_bps: float
    sec_fee_bps: float

    @property
    def total_usd(self) -> float:
        return self.commission_usd + self.spread_usd + self.impact_usd + self.sec_fee_usd

    @property
    def total_bps(self) -> float:
        return self.commission_bps + self.spread_bps + self.impact_bps + self.sec_fee_bps


def _commission_for(cfg: TransactionCostConfig, *, shares: float, trade_value_usd: float) -> float:
    """Return broker-side commission USD per the configured model."""
    abs_shares = abs(shares)
    abs_value = abs(trade_value_usd)
    if abs_shares == 0 or abs_value == 0:
        return 0.0
    if cfg.commission_model == "zero":
        return 0.0
    if cfg.commission_model == "ibkr_lite":
        # IBKR Lite is commission-free for US stocks; treat as 0.
        return 0.0
    if cfg.commission_model == "fixed_per_share":
        return (abs_shares * cfg.fixed_per_share_cents) / 100.0
    # Default: ibkr_tiered.
    raw = (abs_shares * cfg.cents_per_share) / 100.0
    capped_low = max(raw, cfg.min_commission_usd)
    capped_high = min(capped_low, abs_value * cfg.max_commission_pct_of_trade)
    return capped_high


def _bps(usd: float, trade_value_usd: float) -> float:
    if trade_value_usd <= 0:
        return 0.0
    return (usd / trade_value_usd) * 10_000.0


def estimate_trade_cost(
    *,
    shares: float,
    price: float,
    adv_usd: float,
    cfg: TransactionCostConfig,
    is_sell: bool = False,
) -> TradeCost:
    """Estimate per-trade cost in USD + bps.

    Args:
        shares: signed share count (sign is ignored by the cost model except
            the SEC fee, which only applies on sells/short-covers).
        price: assumed fill price (signal price is fine for v1).
        adv_usd: 20-day average dollar volume for the impact term. ``0`` or
            ``NaN`` are tolerated and produce zero impact (with a flag).
        cfg: ``TransactionCostConfig`` from ``Config.portfolio.transaction_cost``.
        is_sell: True when the trade reduces inventory (sell / short-cover);
            controls SEC fee application.

    Returns:
        TradeCost with the full decomposition.
    """
    abs_shares = abs(shares)
    trade_value_usd = abs_shares * max(price, 0.0)

    commission_usd = _commission_for(cfg, shares=abs_shares, trade_value_usd=trade_value_usd)
    sec_fee_usd = 0.0
    if is_sell and trade_value_usd > 0:
        sec_fee_usd = trade_value_usd * (cfg.sec_fee_bps / 10_000.0)

    # Half-spread: the full bid/ask spread costs you spread/2 per side, but
    # config calibration is round-trip-bps so we apply spread_bps directly.
    spread_usd = trade_value_usd * (cfg.avg_spread_bps / 10_000.0)

    if trade_value_usd > 0 and adv_usd and adv_usd > 0 and not math.isnan(adv_usd):
        impact_bps = cfg.impact_coef_bps * math.sqrt(trade_value_usd / adv_usd)
    else:
        impact_bps = 0.0
    impact_usd = trade_value_usd * (impact_bps / 10_000.0)

    return TradeCost(
        commission_usd=commission_usd,
        spread_usd=spread_usd,
        impact_usd=impact_usd,
        sec_fee_usd=sec_fee_usd,
        commission_bps=_bps(commission_usd, trade_value_usd),
        spread_bps=cfg.avg_spread_bps if trade_value_usd > 0 else 0.0,
        impact_bps=impact_bps,
        sec_fee_bps=cfg.sec_fee_bps if (is_sell and trade_value_usd > 0) else 0.0,
    )


__all__ = ["TradeCost", "estimate_trade_cost"]
