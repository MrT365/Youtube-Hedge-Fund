"""SCORE-02 value factor.

The module emits raw long-format rows for the Phase 2 orchestrator. It does not
rank values; sector-neutral percentile ranking happens downstream.

Plan-level assumptions:
  - Enterprise value uses long-term debt only because L1 fundamentals do not
    store short-term debt.
  - The enterprise-value earnings proxy is EBIT over EV because L1 fundamentals
    do not store depreciation and amortization.

Sub-factors:
  - val_fwd_ey: eps_fy1 / close
  - val_bp: total_equity / market_cap
  - val_fcf_yield: fundamental_ratios.fcf_yield
  - val_ev_ebit_inv: ebit / enterprise_value
  - val_shareholder_yield: dividend_yield + buyback_yield
  - val_sales_ev: revenue / enterprise_value
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors._pit import (
    latest_close_pit,
    latest_estimates_pit,
    latest_fundamentals_pit,
)
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "val_fwd_ey",
    "val_bp",
    "val_fcf_yield",
    "val_ev_ebit_inv",
    "val_shareholder_yield",
    "val_sales_ev",
)


@register_factor("value")
def compute_value(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return columns ``ticker``, ``sub_factor``, ``raw_value`` for value."""
    universe = _load_universe_tickers(conn, tickers)
    if not universe:
        return _empty_result()

    ratios_by_ticker = _load_latest_ratios(conn, universe, asof)

    rows: list[dict[str, object]] = []
    for ticker in universe:
        values = _compute_one(conn, asof, ticker, ratios_by_ticker.get(ticker, {}))
        rows.extend(
            {"ticker": ticker, "sub_factor": sub_factor, "raw_value": values[sub_factor]}
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows, columns=["ticker", "sub_factor", "raw_value"])
    out["raw_value"] = out["raw_value"].astype("float64")
    log.info("compute_value_complete", n_tickers=len(universe), n_rows=len(out))
    return out


def _load_universe_tickers(conn: sqlite3.Connection, tickers: list[str] | None) -> list[str]:
    query = "SELECT ticker FROM universe"
    params: list[str] = []
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        query += f" WHERE ticker IN ({placeholders})"
        params.extend(tickers)
    query += " ORDER BY ticker"
    return [str(row[0]) for row in conn.execute(query, params).fetchall()]


def _load_latest_ratios(
    conn: sqlite3.Connection,
    tickers: list[str],
    asof: date,
) -> dict[str, dict[str, Any]]:
    if not tickers:
        return {}

    placeholders = ",".join("?" * len(tickers))
    cur = conn.execute(
        f"""
        SELECT fr.*
        FROM fundamental_ratios fr
        JOIN (
            SELECT ticker, MAX(asof_date) AS max_asof_date
            FROM fundamental_ratios
            WHERE ticker IN ({placeholders}) AND asof_date <= ?
            GROUP BY ticker
        ) latest
          ON latest.ticker = fr.ticker
         AND latest.max_asof_date = fr.asof_date
        ORDER BY fr.ticker
        """,
        [*tickers, asof.isoformat()],
    )
    rows = cur.fetchall()
    if not rows:
        return {}
    cols = [desc[0] for desc in cur.description]
    return {str(row[0]): dict(zip(cols, row, strict=True)) for row in rows}


def _compute_one(
    conn: sqlite3.Connection,
    asof: date,
    ticker: str,
    ratios: dict[str, Any],
) -> dict[str, float]:
    fund_rows = latest_fundamentals_pit(conn, ticker, "annual", asof, n=1)
    fundamentals = fund_rows[0] if fund_rows else {}
    estimates = latest_estimates_pit(conn, ticker, asof) or {}
    close = latest_close_pit(conn, ticker, asof)

    market_cap = _safe_product(close, fundamentals.get("shares_outstanding"))
    enterprise_value = _enterprise_value(
        market_cap,
        fundamentals.get("long_term_debt"),
        fundamentals.get("cash_and_equivalents"),
    )

    return {
        "val_fwd_ey": _safe_div(estimates.get("eps_fy1"), close),
        "val_bp": _safe_div(fundamentals.get("total_equity"), market_cap),
        "val_fcf_yield": _float_or_nan(ratios.get("fcf_yield")),
        "val_ev_ebit_inv": _safe_div(fundamentals.get("ebit"), enterprise_value),
        "val_shareholder_yield": _shareholder_yield(
            ratios.get("dividend_yield"),
            ratios.get("buyback_yield"),
        ),
        "val_sales_ev": _safe_div(fundamentals.get("revenue"), enterprise_value),
    }


def _enterprise_value(
    market_cap: float,
    long_term_debt: object,
    cash_and_equivalents: object,
) -> float:
    market_cap_f = _float_or_nan(market_cap)
    debt_f = _float_or_nan(long_term_debt)
    cash_f = _float_or_nan(cash_and_equivalents)
    if np.isnan(market_cap_f) or np.isnan(debt_f) or np.isnan(cash_f):
        return float("nan")
    return market_cap_f + debt_f - cash_f


def _safe_product(left: object, right: object) -> float:
    left_f = _float_or_nan(left)
    right_f = _float_or_nan(right)
    if np.isnan(left_f) or np.isnan(right_f):
        return float("nan")
    return left_f * right_f


def _safe_div(numerator: object, denominator: object) -> float:
    numerator_f = _float_or_nan(numerator)
    denominator_f = _float_or_nan(denominator)
    if np.isnan(numerator_f) or np.isnan(denominator_f) or denominator_f == 0.0:
        return float("nan")
    return numerator_f / denominator_f


def _shareholder_yield(dividend_yield: object, buyback_yield: object) -> float:
    dividend_f = _float_or_nan(dividend_yield)
    buyback_f = _float_or_nan(buyback_yield)
    if np.isnan(dividend_f) or np.isnan(buyback_f):
        return float("nan")
    return dividend_f + buyback_f


def _float_or_nan(value: object) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    return float(cast("float | int | str", value))


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_value"]
