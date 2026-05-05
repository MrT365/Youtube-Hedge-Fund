"""Factor-exposure calculator (PORT-08).

Weighted average of each base factor (momentum/value/quality/growth/revisions/
short_interest/insider/institutional) across the long book vs the short book,
plus the long-short spread per factor. The "1σ from historical" flag is a
Phase 9 add-on (needs a rolling history of book-level exposure); v1 just emits
the live spread.

Inputs:
  * weights: signed Series indexed by ticker (positive=long, negative=short)
  * conn:    sqlite connection — reads ``factor_scores_parent`` for the asof
             date with one row per (ticker, factor, parent_score)

Output:
  DataFrame indexed by factor name with columns
  [long_avg, short_avg, ls_spread, n_long, n_short]

  ``ls_spread = long_avg - short_avg``  — positive means long book is more
  exposed to that factor than the short book (i.e., the strategy is "long
  good momentum / short bad momentum" if ls_spread on momentum > 0).
"""

from __future__ import annotations

import sqlite3
from datetime import date as date_type

import pandas as pd

BASE_FACTORS = (
    "momentum", "value", "quality", "growth", "revisions",
    "short_interest", "insider", "institutional",
)


def compute_factor_exposure(
    conn: sqlite3.Connection,
    *,
    weights: pd.Series,
    asof: date_type,
) -> pd.DataFrame:
    """Return per-factor long/short/spread exposure for the current target book.

    Empty weights → empty DataFrame with the expected columns.
    """
    columns = ["long_avg", "short_avg", "ls_spread", "n_long", "n_short"]
    if weights.empty:
        return pd.DataFrame(columns=columns)
    tickers = list(weights.index)
    placeholders = ",".join("?" * len(tickers))
    df = pd.read_sql_query(
        f"""
        SELECT ticker, factor, parent_score
        FROM factor_scores_parent
        WHERE score_date = ?
          AND factor IN ({",".join("?" * len(BASE_FACTORS))})
          AND ticker IN ({placeholders})
        """,
        conn,
        params=[asof.isoformat(), *BASE_FACTORS, *tickers],
    )
    if df.empty:
        return pd.DataFrame(columns=columns)
    df["parent_score"] = pd.to_numeric(df["parent_score"], errors="coerce")
    df = df.dropna(subset=["parent_score"])

    longs = weights[weights > 0]
    shorts = weights[weights < 0]
    long_gross = float(longs.abs().sum())
    short_gross = float(shorts.abs().sum())

    rows = []
    for factor in BASE_FACTORS:
        sub = df[df["factor"] == factor].set_index("ticker")["parent_score"]
        if sub.empty:
            rows.append({
                "factor": factor, "long_avg": float("nan"), "short_avg": float("nan"),
                "ls_spread": float("nan"), "n_long": 0, "n_short": 0,
            })
            continue

        long_aligned = sub.reindex(longs.index).dropna()
        short_aligned = sub.reindex(shorts.index).dropna()

        long_avg = (
            float((long_aligned * longs.reindex(long_aligned.index).abs()).sum() / long_gross)
            if long_gross > 0 and not long_aligned.empty
            else float("nan")
        )
        short_avg = (
            float((short_aligned * shorts.reindex(short_aligned.index).abs()).sum() / short_gross)
            if short_gross > 0 and not short_aligned.empty
            else float("nan")
        )
        ls_spread = (
            long_avg - short_avg
            if not pd.isna(long_avg) and not pd.isna(short_avg)
            else float("nan")
        )
        rows.append({
            "factor": factor,
            "long_avg": long_avg,
            "short_avg": short_avg,
            "ls_spread": ls_spread,
            "n_long": len(long_aligned),
            "n_short": len(short_aligned),
        })
    return pd.DataFrame(rows).set_index("factor")


__all__ = ["BASE_FACTORS", "compute_factor_exposure"]
