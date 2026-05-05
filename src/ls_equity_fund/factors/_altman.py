"""Altman Z-Score implementation for SCORE-03 quality.

Plan decision A3 uses the original public-manufacturing formula across all
sectors for v1 rather than Z' or Z'' variants.
"""

from __future__ import annotations

from typing import Any

ALTMAN_COEFFS: tuple[float, float, float, float, float] = (1.2, 1.4, 3.3, 0.6, 1.0)


def _value(row: dict[str, Any], key: str) -> float | None:
    raw = row.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compute_altman_z(fund_row: dict[str, Any], market_cap: float | None) -> float | None:
    """Return original Altman Z-Score or ``None`` when critical inputs are unavailable."""
    total_assets = _value(fund_row, "total_assets")
    total_liabilities = _value(fund_row, "total_liabilities")
    working_capital = _value(fund_row, "working_capital")
    retained_earnings = _value(fund_row, "retained_earnings")
    ebit = _value(fund_row, "ebit")
    revenue = _value(fund_row, "revenue")

    if (
        total_assets is None
        or total_assets == 0.0
        or total_liabilities is None
        or total_liabilities == 0.0
        or market_cap is None
        or working_capital is None
        or retained_earnings is None
        or ebit is None
        or revenue is None
    ):
        return None

    try:
        market_value = float(market_cap)
    except (TypeError, ValueError):
        return None

    x1 = working_capital / total_assets
    x2 = retained_earnings / total_assets
    x3 = ebit / total_assets
    x4 = market_value / total_liabilities
    x5 = revenue / total_assets
    c1, c2, c3, c4, c5 = ALTMAN_COEFFS
    return c1 * x1 + c2 * x2 + c3 * x3 + c4 * x4 + c5 * x5


def classify_zone(z: float | None) -> str | None:
    """Return Altman zone label for a Z-Score."""
    if z is None:
        return None
    if z > 2.99:
        return "safe"
    if z >= 1.81:
        return "grey"
    return "distress"


__all__ = ["ALTMAN_COEFFS", "classify_zone", "compute_altman_z"]
