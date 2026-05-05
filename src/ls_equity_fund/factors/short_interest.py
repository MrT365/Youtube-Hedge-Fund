"""SCORE-06 short-interest factor.

Short-interest sub-factors are persisted with long-side semantics. High short
interest is bearish for longs, so every emitted raw value is sign-flipped before
sector ranking. Later portfolio layers derive short-side scores as
``100 - long_side_score`` rather than duplicating side-specific rows.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors._pit import universe_tickers
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "short_pct_float_inv",
    "short_dtc_inv",
    "short_change_inv",
)


@register_factor("short_interest")
def compute_short_interest(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return columns ``ticker``, ``sub_factor``, ``raw_value`` for SCORE-06."""
    target_tickers = universe_tickers(conn, tickers)
    if not target_tickers:
        return _empty_result()

    rows: list[dict[str, object]] = []
    prior_cutoff = asof - timedelta(days=30)
    for ticker in target_tickers:
        latest = _latest_short_interest_pit(conn, ticker, asof)
        prior = _latest_short_interest_pit(conn, ticker, prior_cutoff)
        values = _compute_one(latest, prior)
        rows.extend(
            {"ticker": ticker, "sub_factor": sub_factor, "raw_value": values[sub_factor]}
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows, columns=["ticker", "sub_factor", "raw_value"])
    out["raw_value"] = out["raw_value"].astype("float64")
    log.info("compute_short_interest_complete", n_tickers=len(target_tickers), n_rows=len(out))
    return out


def _latest_short_interest_pit(
    conn: sqlite3.Connection,
    ticker: str,
    asof: date,
) -> dict[str, Any] | None:
    cur = conn.execute(
        """
        SELECT short_percent_of_float, short_ratio
        FROM short_interest
        WHERE ticker = ? AND snapshot_date <= ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (ticker, asof.isoformat()),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "short_percent_of_float": row[0],
        "short_ratio": row[1],
    }


def _compute_one(
    latest: dict[str, Any] | None,
    prior: dict[str, Any] | None,
) -> dict[str, float]:
    if latest is None:
        return {sub_factor: np.nan for sub_factor in SUB_FACTORS}

    short_percent_of_float = _optional_float(latest["short_percent_of_float"])
    short_ratio = _optional_float(latest["short_ratio"])
    prior_short_percent_of_float = (
        None if prior is None else _optional_float(prior["short_percent_of_float"])
    )

    short_pct_float_inv = -short_percent_of_float if short_percent_of_float is not None else np.nan
    short_dtc_inv = -short_ratio if short_ratio is not None else np.nan
    short_change_inv = (
        prior_short_percent_of_float - short_percent_of_float
        if prior_short_percent_of_float is not None and short_percent_of_float is not None
        else np.nan
    )

    return {
        "short_pct_float_inv": short_pct_float_inv,
        "short_dtc_inv": short_dtc_inv,
        "short_change_inv": short_change_inv,
    }


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_short_interest"]
