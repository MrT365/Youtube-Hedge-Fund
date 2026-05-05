"""Transaction-cost model tests (PORT-04)."""

from __future__ import annotations

import math

from ls_equity_fund.config import TransactionCostConfig
from ls_equity_fund.portfolio.transaction_cost import estimate_trade_cost


def _cfg(**overrides):  # type: ignore[no-untyped-def]
    return TransactionCostConfig(**overrides)


def test_ibkr_tiered_minimum_floor() -> None:
    """100 shares × $0.0035 = $0.35 = minimum, so capped at $0.35 floor."""
    cost = estimate_trade_cost(shares=100, price=10.0, adv_usd=1_000_000, cfg=_cfg())
    assert math.isclose(cost.commission_usd, 0.35, abs_tol=1e-6)


def test_ibkr_tiered_max_pct_of_trade() -> None:
    """Cheap micro-cap: 5,000 shares × $0.10 = $500 trade value.
    Tiered raw = 5000 * 0.0035 = $17.50, capped at 1% × $500 = $5."""
    cost = estimate_trade_cost(shares=5000, price=0.10, adv_usd=10_000, cfg=_cfg())
    assert math.isclose(cost.commission_usd, 5.0, rel_tol=1e-9)


def test_ibkr_lite_zero_commission() -> None:
    cost = estimate_trade_cost(
        shares=100,
        price=100.0,
        adv_usd=1_000_000,
        cfg=_cfg(commission_model="ibkr_lite"),
    )
    assert cost.commission_usd == 0.0


def test_zero_model_zero_commission() -> None:
    cost = estimate_trade_cost(
        shares=100,
        price=100.0,
        adv_usd=1_000_000,
        cfg=_cfg(commission_model="zero"),
    )
    assert cost.commission_usd == 0.0


def test_fixed_per_share_model() -> None:
    """100 shares × $0.005 = $0.50."""
    cost = estimate_trade_cost(
        shares=100,
        price=100.0,
        adv_usd=1_000_000,
        cfg=_cfg(commission_model="fixed_per_share", fixed_per_share_cents=0.5),
    )
    assert math.isclose(cost.commission_usd, 0.50, rel_tol=1e-9)


def test_spread_bps_constant_per_trade() -> None:
    """4 bps default = $40 on $100k trade."""
    cost = estimate_trade_cost(
        shares=1000,
        price=100.0,
        adv_usd=10_000_000,
        cfg=_cfg(),
    )
    assert math.isclose(cost.spread_usd, 100_000 * 4 / 10_000, rel_tol=1e-9)
    assert cost.spread_bps == 4.0


def test_impact_sqrt_law() -> None:
    """impact_bps = coef × sqrt(notional/adv). 1% participation → 1 bp baseline."""
    cfg = _cfg(impact_coef_bps=10.0, avg_spread_bps=0.0)
    # 100k notional, 10M adv → ratio 0.01 → sqrt 0.1 → 10 * 0.1 = 1 bp
    cost = estimate_trade_cost(
        shares=1000,
        price=100.0,
        adv_usd=10_000_000,
        cfg=cfg,
    )
    assert math.isclose(cost.impact_bps, 1.0, rel_tol=1e-6)
    assert math.isclose(cost.impact_usd, 100_000 * 1.0 / 10_000, rel_tol=1e-6)


def test_impact_zero_when_no_adv() -> None:
    cost = estimate_trade_cost(
        shares=1000,
        price=100.0,
        adv_usd=0.0,
        cfg=_cfg(),
    )
    assert cost.impact_bps == 0.0


def test_sec_fee_only_on_sells() -> None:
    cfg = _cfg(sec_fee_bps=2.0, avg_spread_bps=0.0, impact_coef_bps=0.0)
    buy = estimate_trade_cost(shares=1000, price=10.0, adv_usd=1_000_000, cfg=cfg, is_sell=False)
    sell = estimate_trade_cost(shares=1000, price=10.0, adv_usd=1_000_000, cfg=cfg, is_sell=True)
    assert buy.sec_fee_usd == 0.0
    # 10k notional × 2 bps = $2
    assert math.isclose(sell.sec_fee_usd, 2.0, rel_tol=1e-9)


def test_total_decomposition_sums() -> None:
    cost = estimate_trade_cost(
        shares=1000,
        price=50.0,
        adv_usd=1_000_000,
        cfg=_cfg(),
        is_sell=True,
    )
    s = cost.commission_usd + cost.spread_usd + cost.impact_usd + cost.sec_fee_usd
    assert math.isclose(cost.total_usd, s, rel_tol=1e-12)
