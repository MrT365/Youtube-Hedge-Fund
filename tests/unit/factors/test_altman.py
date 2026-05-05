"""Altman Z-Score tests for SCORE-03 quality."""

from __future__ import annotations

import pytest

from ls_equity_fund.factors._altman import ALTMAN_COEFFS, classify_zone, compute_altman_z


def _fund(**overrides: float | None) -> dict[str, float | None]:
    row: dict[str, float | None] = {
        "working_capital": 1.0,
        "retained_earnings": 2.0,
        "ebit": 3.0,
        "total_assets": 10.0,
        "total_liabilities": 10.0,
        "revenue": 5.0,
    }
    row.update(overrides)
    return row


def test_coefficients() -> None:
    assert ALTMAN_COEFFS == (1.2, 1.4, 3.3, 0.6, 1.0)


def test_formula_coefficients() -> None:
    assert compute_altman_z(_fund(), market_cap=4.0) == pytest.approx(2.13)


def test_zone_safe() -> None:
    assert classify_zone(3.0) == "safe"
    assert classify_zone(2.99) == "grey"
    assert classify_zone(3.01) == "safe"


def test_zone_grey() -> None:
    assert classify_zone(2.0) == "grey"
    assert classify_zone(1.81) == "grey"
    assert classify_zone(1.80) == "distress"


def test_zone_distress() -> None:
    assert classify_zone(1.0) == "distress"
    assert classify_zone(0.0) == "distress"
    assert classify_zone(-1.0) == "distress"


def test_none_when_total_assets_zero() -> None:
    assert compute_altman_z(_fund(total_assets=0.0), market_cap=4.0) is None


def test_none_when_total_liabilities_zero() -> None:
    assert compute_altman_z(_fund(total_liabilities=0.0), market_cap=4.0) is None

