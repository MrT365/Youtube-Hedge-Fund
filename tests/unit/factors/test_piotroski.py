"""Piotroski F-Score tests for SCORE-03 quality."""

from __future__ import annotations

from typing import Any

from ls_equity_fund.factors._piotroski import PIOTROSKI_CHECKS, compute_piotroski_f


def _current(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "net_income": 100.0,
        "cfo": 120.0,
        "total_assets": 1_000.0,
        "long_term_debt": 100.0,
        "current_assets": 400.0,
        "current_liabilities": 200.0,
        "shares_outstanding": 90.0,
        "gross_profit": 500.0,
        "revenue": 1_000.0,
    }
    row.update(overrides)
    return row


def _prior(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "net_income": 80.0,
        "cfo": 70.0,
        "total_assets": 1_000.0,
        "long_term_debt": 200.0,
        "current_assets": 300.0,
        "current_liabilities": 200.0,
        "shares_outstanding": 100.0,
        "gross_profit": 360.0,
        "revenue": 900.0,
    }
    row.update(overrides)
    return row


def test_check_count() -> None:
    assert len(PIOTROSKI_CHECKS) == 9


def test_perfect_9() -> None:
    assert compute_piotroski_f(_current(), _prior()) == 9


def test_zero() -> None:
    current = _current(
        net_income=-10.0,
        cfo=-20.0,
        total_assets=1_000.0,
        long_term_debt=300.0,
        current_assets=100.0,
        current_liabilities=200.0,
        shares_outstanding=110.0,
        gross_profit=300.0,
        revenue=1_000.0,
    )
    prior = _prior(
        net_income=10.0,
        total_assets=100.0,
        long_term_debt=10.0,
        current_assets=400.0,
        current_liabilities=200.0,
        gross_profit=500.0,
        revenue=1_000.0,
    )

    assert compute_piotroski_f(current, prior) == 0


def test_f1_positive_ni() -> None:
    assert compute_piotroski_f(_current(net_income=1.0), _prior(net_income=0.0)) == 9
    assert compute_piotroski_f(_current(net_income=0.0), _prior(net_income=-1.0)) == 8


def test_f2_positive_cfo() -> None:
    assert compute_piotroski_f(_current(cfo=1.0, net_income=0.5), _prior(net_income=0.0)) == 9
    assert compute_piotroski_f(_current(cfo=0.0), _prior()) == 7


def test_f3_improving_roa() -> None:
    assert compute_piotroski_f(_current(net_income=100.0), _prior(net_income=80.0)) == 9
    assert compute_piotroski_f(_current(net_income=70.0), _prior(net_income=80.0)) == 8


def test_f4_cfo_gt_ni() -> None:
    assert compute_piotroski_f(_current(cfo=120.0, net_income=100.0), _prior()) == 9
    assert compute_piotroski_f(_current(cfo=90.0, net_income=100.0), _prior()) == 8


def test_f5_decreasing_leverage() -> None:
    assert compute_piotroski_f(_current(long_term_debt=100.0), _prior(long_term_debt=200.0)) == 9
    assert compute_piotroski_f(_current(long_term_debt=300.0), _prior(long_term_debt=200.0)) == 8


def test_f6_improving_current_ratio() -> None:
    assert compute_piotroski_f(_current(current_assets=400.0), _prior(current_assets=300.0)) == 9
    assert compute_piotroski_f(_current(current_assets=200.0), _prior(current_assets=300.0)) == 8


def test_f7_no_new_shares() -> None:
    assert compute_piotroski_f(_current(shares_outstanding=100.0), _prior()) == 9
    assert compute_piotroski_f(_current(shares_outstanding=101.0), _prior()) == 8


def test_f8_improving_gm() -> None:
    assert compute_piotroski_f(_current(gross_profit=500.0), _prior(gross_profit=400.0)) == 9
    assert compute_piotroski_f(_current(gross_profit=300.0), _prior(gross_profit=400.0)) == 8


def test_f9_improving_asset_turnover() -> None:
    assert compute_piotroski_f(_current(revenue=1_100.0), _prior(revenue=1_000.0)) == 9
    assert compute_piotroski_f(_current(revenue=900.0), _prior(revenue=1_000.0)) == 8


def test_none_when_prior_missing() -> None:
    assert compute_piotroski_f(_current(), None) is None


def test_none_when_critical_field_missing() -> None:
    assert compute_piotroski_f(_current(total_assets=None), _prior()) is None
