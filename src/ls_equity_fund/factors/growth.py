"""SCORE-04 growth factor.

The module emits raw long-format sub-factor rows only. Ranking and parent-score
composition are owned by the shared Phase 2 scoring pipeline.

Sub-factors:
  - grow_rev_yoy: fundamental_ratios.revenue_growth_yoy
  - grow_earn_yoy: fundamental_ratios.earnings_growth_yoy
  - grow_rev_accel: revenue_growth_yoy[t] - revenue_growth_yoy[t-1y]
  - grow_rd_intensity: fundamental_ratios.rd_intensity
  - grow_fcf_yoy: (fcf[t] - fcf[t-4q]) / abs(fcf[t-4q])
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors._pit import latest_fundamentals_pit, universe_tickers
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "grow_rev_yoy",
    "grow_earn_yoy",
    "grow_rev_accel",
    "grow_rd_intensity",
    "grow_fcf_yoy",
)


@register_factor("growth")
def compute_growth(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return columns ``ticker``, ``sub_factor``, ``raw_value`` for growth."""
    target_tickers = universe_tickers(conn, tickers)
    if not target_tickers:
        return _empty_result()

    current_ratios = _load_ratio_snapshot(conn, target_tickers, asof)
    prior_ratios = _load_ratio_snapshot(conn, target_tickers, asof - timedelta(days=365))

    rows: list[dict[str, object]] = []
    for ticker in target_tickers:
        cur = current_ratios.get(ticker, {})
        prior = prior_ratios.get(ticker, {})

        values = {
            "grow_rev_yoy": _to_float(cur.get("revenue_growth_yoy")),
            "grow_earn_yoy": _to_float(cur.get("earnings_growth_yoy")),
            "grow_rev_accel": _difference(
                cur.get("revenue_growth_yoy"),
                prior.get("revenue_growth_yoy"),
            ),
            "grow_rd_intensity": _to_float(cur.get("rd_intensity")),
            "grow_fcf_yoy": _fcf_yoy(conn, ticker, asof),
        }
        rows.extend(
            {"ticker": ticker, "sub_factor": sub_factor, "raw_value": values[sub_factor]}
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows, columns=["ticker", "sub_factor", "raw_value"])
    out["raw_value"] = out["raw_value"].astype("float64")
    log.info("compute_growth_complete", n_tickers=len(target_tickers), n_rows=len(out))
    return out


def _load_ratio_snapshot(
    conn: sqlite3.Connection,
    tickers: list[str],
    asof: date,
) -> dict[str, dict[str, Any]]:
    """Return latest ``fundamental_ratios`` row per ticker at or before ``asof``."""
    if not tickers:
        return {}

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
        ORDER BY r.ticker
        """,
        [*tickers, asof.isoformat()],
    )
    cols = [desc[0] for desc in cur.description]
    return {
        str(row[cols.index("ticker")]): dict(zip(cols, row, strict=True))
        for row in cur.fetchall()
    }


def _fcf_yoy(conn: sqlite3.Connection, ticker: str, asof: date) -> float:
    rows = latest_fundamentals_pit(conn, ticker, "quarterly", asof, n=5)
    if len(rows) < 5:
        return float("nan")
    current = _to_float(rows[0].get("free_cash_flow"))
    prior = _to_float(rows[4].get("free_cash_flow"))
    if np.isnan(current) or np.isnan(prior):
        return float("nan")
    denominator = abs(prior)
    if denominator == 0.0:
        return float("nan")
    return (current - prior) / denominator


def _difference(current: Any, prior: Any) -> float:
    cur = _to_float(current)
    prv = _to_float(prior)
    if np.isnan(cur) or np.isnan(prv):
        return float("nan")
    return cur - prv


def _to_float(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if not np.isnan(out) else float("nan")


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_growth"]
