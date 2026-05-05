"""Piotroski F-Score implementation for SCORE-03 quality.

The score sums nine binary checks across current and prior annual fundamentals.
It returns ``None`` when the prior row or critical fields are missing so the
caller can preserve audit-visible missingness instead of silently imputing zero.
"""

from __future__ import annotations

from typing import Any, cast

PIOTROSKI_CHECKS: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9")
_CHECK_DESCRIPTIONS: tuple[str, ...] = (
    "F1 positive net income",
    "F2 positive cash flow from operations",
    "F3 improving return on assets",
    "F4 cash flow from operations greater than net income",
    "F5 decreasing long-term-debt leverage",
    "F6 improving current ratio",
    "F7 no new shares issued",
    "F8 improving gross margin",
    "F9 improving asset turnover",
)

_REQUIRED_FIELDS: tuple[str, ...] = (
    "net_income",
    "cfo",
    "total_assets",
    "long_term_debt",
    "current_assets",
    "current_liabilities",
    "shares_outstanding",
    "gross_profit",
    "revenue",
)


def _value(row: dict[str, Any], key: str) -> float | None:
    raw = row.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0.0:
        return None
    return numerator / denominator


def compute_piotroski_f(current: dict[str, Any], prior: dict[str, Any] | None) -> int | None:
    """Return Piotroski F-Score in ``0..9`` or ``None`` if required inputs are absent."""
    if prior is None:
        return None

    current_values = {field: _value(current, field) for field in _REQUIRED_FIELDS}
    prior_values = {field: _value(prior, field) for field in _REQUIRED_FIELDS}
    if any(value is None for value in current_values.values()) or any(
        value is None for value in prior_values.values()
    ):
        return None

    cur = cast("dict[str, float]", current_values)
    prv = cast("dict[str, float]", prior_values)

    ni_t = cur["net_income"]
    ni_p = prv["net_income"]
    cfo_t = cur["cfo"]
    ta_t = cur["total_assets"]
    ta_p = prv["total_assets"]
    ltd_t = cur["long_term_debt"]
    ltd_p = prv["long_term_debt"]
    ca_t = cur["current_assets"]
    ca_p = prv["current_assets"]
    cl_t = cur["current_liabilities"]
    cl_p = prv["current_liabilities"]
    shares_t = cur["shares_outstanding"]
    shares_p = prv["shares_outstanding"]
    gp_t = cur["gross_profit"]
    gp_p = prv["gross_profit"]
    rev_t = cur["revenue"]
    rev_p = prv["revenue"]

    roa_t = _safe_div(ni_t, ta_t)
    roa_p = _safe_div(ni_p, ta_p)
    leverage_t = _safe_div(ltd_t, ta_t)
    leverage_p = _safe_div(ltd_p, ta_p)
    current_ratio_t = _safe_div(ca_t, cl_t)
    current_ratio_p = _safe_div(ca_p, cl_p)
    gross_margin_t = _safe_div(gp_t, rev_t)
    gross_margin_p = _safe_div(gp_p, rev_p)
    asset_turnover_t = _safe_div(rev_t, ta_t)
    asset_turnover_p = _safe_div(rev_p, ta_p)

    f1 = int(ni_t > 0.0)
    f2 = int(cfo_t > 0.0)
    f3 = int(roa_t is not None and roa_p is not None and roa_t > roa_p)
    f4 = int(cfo_t > ni_t)
    f5 = int(leverage_t is not None and leverage_p is not None and leverage_t < leverage_p)
    f6 = int(
        current_ratio_t is not None
        and current_ratio_p is not None
        and current_ratio_t > current_ratio_p
    )
    f7 = int(shares_t <= shares_p)
    f8 = int(
        gross_margin_t is not None and gross_margin_p is not None and gross_margin_t > gross_margin_p
    )
    f9 = int(
        asset_turnover_t is not None
        and asset_turnover_p is not None
        and asset_turnover_t > asset_turnover_p
    )

    return f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9


__all__ = ["PIOTROSKI_CHECKS", "compute_piotroski_f"]
