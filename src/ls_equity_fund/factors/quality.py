"""SCORE-03 quality factor.

The factor emits raw long-format rows only. Sector-neutral ranking and parent
score composition happen in the shared Phase 2 infrastructure.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors._altman import compute_altman_z
from ls_equity_fund.factors._piotroski import compute_piotroski_f
from ls_equity_fund.factors._pit import latest_close_pit, latest_fundamentals_pit, universe_tickers
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "qual_roe_stability",
    "qual_gm_level",
    "qual_gm_trend",
    "qual_de_inv",
    "qual_cfo_ni",
    "qual_accruals_inv",
    "qual_piotroski_f",
    "qual_altman_z",
)

_ROE_STABILITY_EPS = 1e-9


@register_factor("quality")
def compute_quality(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return columns ``ticker``, ``sub_factor``, ``raw_value`` for quality."""
    target_tickers = universe_tickers(conn, tickers)
    if not target_tickers:
        return _empty_result()

    ratios_by_ticker = _latest_ratios(conn, asof, target_tickers)

    rows: list[dict[str, object]] = []
    for ticker in target_tickers:
        values = _compute_one(conn, asof, ticker, ratios_by_ticker.get(ticker))
        rows.extend(
            {"ticker": ticker, "sub_factor": sub_factor, "raw_value": values[sub_factor]}
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows, columns=["ticker", "sub_factor", "raw_value"])
    out["raw_value"] = out["raw_value"].astype("float64")
    log.info("compute_quality_complete", n_tickers=len(target_tickers), n_rows=len(out))
    return out


def _compute_one(
    conn: sqlite3.Connection,
    asof: date,
    ticker: str,
    ratios: dict[str, Any] | None,
) -> dict[str, float]:
    quarterly = latest_fundamentals_pit(conn, ticker, "quarterly", asof, n=8)
    annual = latest_fundamentals_pit(conn, ticker, "annual", asof, n=2)
    current = annual[0] if annual else None
    prior = annual[1] if len(annual) >= 2 else None

    gross_margin = _ratio_value(ratios, "gross_margin")
    debt_to_equity = _ratio_value(ratios, "debt_to_equity")
    cfo_to_ni = _ratio_value(ratios, "cfo_to_ni")
    accruals_ratio = _ratio_value(ratios, "accruals_ratio")

    f_score = compute_piotroski_f(current, prior) if current is not None else None
    z_score = compute_altman_z(current, _market_cap(conn, ticker, asof, current)) if current else None
    qual_de_inv = -debt_to_equity if debt_to_equity is not None else np.nan
    qual_accruals_inv = -accruals_ratio if accruals_ratio is not None else np.nan

    return {
        "qual_roe_stability": _roe_stability(quarterly),
        "qual_gm_level": gross_margin,
        "qual_gm_trend": _gross_margin_trend(current, prior),
        "qual_de_inv": qual_de_inv,
        "qual_cfo_ni": cfo_to_ni,
        "qual_accruals_inv": qual_accruals_inv,
        "qual_piotroski_f": float(f_score) if f_score is not None else np.nan,
        "qual_altman_z": float(z_score) if z_score is not None else np.nan,
    }


def _latest_ratios(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str],
) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" * len(tickers))
    cur = conn.execute(
        f"""
        WITH latest AS (
            SELECT ticker, MAX(asof_date) AS asof_date
            FROM fundamental_ratios
            WHERE ticker IN ({placeholders}) AND asof_date <= ?
            GROUP BY ticker
        )
        SELECT r.*
        FROM fundamental_ratios r
        JOIN latest l USING (ticker, asof_date)
        """,
        (*tickers, asof.isoformat()),
    )
    rows = cur.fetchall()
    if not rows:
        return {}
    cols = [desc[0] for desc in cur.description]
    return {str(row[0]): dict(zip(cols, row, strict=True)) for row in rows}


def _ratio_value(ratios: dict[str, Any] | None, key: str) -> float:
    if ratios is None:
        return float("nan")
    value = _to_float(ratios.get(key))
    return value if value is not None else float("nan")


def _roe_stability(quarterly_rows: list[dict[str, Any]]) -> float:
    if len(quarterly_rows) < 8:
        return float("nan")

    roes: list[float] = []
    for row in quarterly_rows:
        roe = _to_float(row.get("roe"))
        if roe is None:
            net_income = _to_float(row.get("net_income"))
            total_equity = _to_float(row.get("total_equity"))
            if net_income is None or total_equity in (None, 0.0):
                return float("nan")
            roe = net_income / total_equity
        roes.append(roe)

    std = float(np.std(roes, ddof=0))
    return 1.0 / (std + _ROE_STABILITY_EPS)


def _gross_margin_trend(current: dict[str, Any] | None, prior: dict[str, Any] | None) -> float:
    if current is None or prior is None:
        return float("nan")
    current_gm = _gross_margin(current)
    prior_gm = _gross_margin(prior)
    if current_gm is None or prior_gm is None:
        return float("nan")
    return current_gm - prior_gm


def _gross_margin(row: dict[str, Any]) -> float | None:
    gross_profit = _to_float(row.get("gross_profit"))
    revenue = _to_float(row.get("revenue"))
    if gross_profit is None or revenue in (None, 0.0):
        return None
    return gross_profit / revenue


def _market_cap(
    conn: sqlite3.Connection,
    ticker: str,
    asof: date,
    current: dict[str, Any] | None,
) -> float | None:
    if current is None:
        return None
    close = latest_close_pit(conn, ticker, asof)
    shares = _to_float(current.get("shares_outstanding"))
    if close is None or shares is None:
        return None
    return close * shares


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_quality"]
