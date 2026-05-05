"""Read-only data access for the dashboard.

The dashboard never writes to SQLite — it only reads ``factor_scores`` and
``factor_scores_parent`` populated by ``meridian run-scoring``. Each query
returns a small pandas DataFrame; widgets render directly off these.

All queries take a ``score_date``; if None, ``latest_score_date`` is used.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

# The 8 base factor names (parent_score rows we pivot for the breakdown view).
# 'combined' is read separately.
BASE_FACTORS: tuple[str, ...] = (
    "momentum",
    "value",
    "quality",
    "growth",
    "revisions",
    "short_interest",
    "insider",
    "institutional",
)


def latest_score_date(conn: sqlite3.Connection) -> date | None:
    """Return the most recent score_date across factor_scores_parent."""
    row = conn.execute("SELECT MAX(score_date) FROM factor_scores_parent").fetchone()
    if row is None or row[0] is None:
        return None
    return date.fromisoformat(row[0])


def available_sectors(conn: sqlite3.Connection, asof: date) -> list[str]:
    """Distinct sectors with combined scores on the asof date."""
    cur = conn.execute(
        "SELECT DISTINCT sector FROM factor_scores_parent "
        "WHERE score_date = ? AND factor = 'combined' "
        "ORDER BY sector",
        (asof.isoformat(),),
    )
    return [row[0] for row in cur.fetchall() if row[0]]


def top_candidates(
    conn: sqlite3.Connection,
    asof: date,
    *,
    top: int = 20,
    sectors: list[str] | None = None,
    min_score: float | None = None,
) -> pd.DataFrame:
    """Top-N tickers by combined parent_score, with optional sector + min-score filters."""
    sql_parts = [
        "SELECT ticker, sector, parent_score AS combined_score, n_subfactors_used",
        "FROM factor_scores_parent",
        "WHERE score_date = ? AND factor = 'combined'",
    ]
    params: list[object] = [asof.isoformat()]

    if sectors:
        placeholders = ",".join("?" * len(sectors))
        sql_parts.append(f"AND sector IN ({placeholders})")
        params.extend(sectors)
    if min_score is not None:
        sql_parts.append("AND parent_score >= ?")
        params.append(float(min_score))

    sql_parts.append("ORDER BY parent_score DESC NULLS LAST")
    sql_parts.append("LIMIT ?")
    params.append(int(top))

    sql = "\n".join(sql_parts)
    df = pd.read_sql_query(sql, conn, params=params)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def factor_breakdown(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str],
) -> pd.DataFrame:
    """Wide-form per-ticker × per-factor parent_score table.

    Rows: ticker
    Cols: each base factor + 'combined'
    Used by the heatmap / table view.
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker", *BASE_FACTORS, "combined"])

    placeholders = ",".join("?" * len(tickers))
    sql = (
        "SELECT ticker, factor, parent_score "
        "FROM factor_scores_parent "
        f"WHERE score_date = ? AND ticker IN ({placeholders})"
    )
    params: list[object] = [asof.isoformat(), *tickers]
    long_df = pd.read_sql_query(sql, conn, params=params)

    if long_df.empty:
        return pd.DataFrame(columns=["ticker", *BASE_FACTORS, "combined"])

    wide = long_df.pivot_table(
        index="ticker",
        columns="factor",
        values="parent_score",
        aggfunc="first",
    ).reset_index()

    # Order columns consistently: ticker, base factors in spec order, combined last.
    ordered_cols = ["ticker", *BASE_FACTORS, "combined"]
    for col in ordered_cols:
        if col not in wide.columns:
            wide[col] = pd.NA
    wide = wide[ordered_cols]

    # Preserve the rank ordering from `tickers` argument.
    wide["__order__"] = wide["ticker"].map({t: i for i, t in enumerate(tickers)})
    wide = wide.sort_values("__order__").drop(columns="__order__").reset_index(drop=True)
    return wide


def sector_distribution(
    conn: sqlite3.Connection,
    asof: date,
    *,
    top: int = 20,
    min_score: float | None = None,
) -> pd.DataFrame:
    """Per-sector ticker counts among the top-N combined candidates."""
    df = top_candidates(conn, asof, top=top, min_score=min_score)
    if df.empty:
        return pd.DataFrame(columns=["sector", "count", "avg_score"])

    grouped = (
        df.groupby("sector", as_index=False)
        .agg(count=("ticker", "size"), avg_score=("combined_score", "mean"))
        .sort_values("count", ascending=False)
    )
    return grouped


def universe_size(conn: sqlite3.Connection) -> int:
    """Active (non-delisted) universe size."""
    row = conn.execute("SELECT COUNT(*) FROM universe WHERE delisted_date IS NULL").fetchone()
    return int(row[0]) if row else 0


def scored_size(conn: sqlite3.Connection, asof: date) -> int:
    """Tickers with a combined score on the asof date."""
    row = conn.execute(
        "SELECT COUNT(*) FROM factor_scores_parent WHERE score_date = ? AND factor = 'combined'",
        (asof.isoformat(),),
    ).fetchone()
    return int(row[0]) if row else 0


__all__ = [
    "BASE_FACTORS",
    "available_sectors",
    "factor_breakdown",
    "latest_score_date",
    "scored_size",
    "sector_distribution",
    "top_candidates",
    "universe_size",
]
