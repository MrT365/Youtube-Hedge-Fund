"""SCORE-05 estimate-revisions factor.

Estimate snapshots are calendar-keyed, so these lookbacks intentionally use
calendar-day offsets rather than trading-day offsets.

Sub-factors:
  - rev_30d: eps_fy1[asof] - eps_fy1[asof - 30 calendar days]
  - rev_60d: eps_fy1[asof] - eps_fy1[asof - 60 calendar days]
  - rev_90d: eps_fy1[asof] - eps_fy1[asof - 90 calendar days]

Degenerate-neutral rule: missing current or prior snapshot returns raw_value=0.0
with sufficient_history=0. This keeps early snapshot-history tickers in the
sector-rank cohort instead of excluding them as NaN.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd
import structlog

from ls_equity_fund.factors._pit import universe_tickers
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = ("rev_30d", "rev_60d", "rev_90d")
LOOKBACKS: dict[str, int] = {
    "rev_30d": 30,
    "rev_60d": 60,
    "rev_90d": 90,
}

_RESULT_COLUMNS = ["ticker", "sub_factor", "raw_value", "sufficient_history"]


@register_factor("revisions")
def compute_revisions(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return long-format estimate-revision rows with audit history flags."""
    target_tickers = universe_tickers(conn, tickers)
    if not target_tickers:
        return _empty_result()

    rows: list[dict[str, object]] = []
    for ticker in target_tickers:
        current_eps = _closest_eps_snapshot_at_or_before(conn, ticker, asof)
        for sub_factor, lookback_days in LOOKBACKS.items():
            prior_eps = _closest_eps_snapshot_at_or_before(
                conn,
                ticker,
                asof - timedelta(days=lookback_days),
            )
            if current_eps is None or prior_eps is None:
                raw_value = 0.0
                sufficient_history = 0
            else:
                raw_value = current_eps - prior_eps
                sufficient_history = 1

            rows.append(
                {
                    "ticker": ticker,
                    "sub_factor": sub_factor,
                    "raw_value": raw_value,
                    "sufficient_history": sufficient_history,
                }
            )

    out = pd.DataFrame(rows, columns=_RESULT_COLUMNS)
    out["raw_value"] = out["raw_value"].astype("float64")
    out["sufficient_history"] = out["sufficient_history"].astype("int64")
    log.info(
        "compute_revisions_complete",
        n_tickers=len(target_tickers),
        n_rows=len(out),
        n_degenerate=int((out["sufficient_history"] == 0).sum()),
    )
    return out


def _closest_eps_snapshot_at_or_before(
    conn: sqlite3.Connection,
    ticker: str,
    target_date: date,
) -> float | None:
    row = conn.execute(
        """
        SELECT eps_fy1 FROM analyst_estimates
        WHERE ticker = ? AND snapshot_date <= ?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        (ticker, target_date.isoformat()),
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
            "sufficient_history": pd.Series(dtype="int64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_revisions"]
