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


# -----------------------------------------------------------------------------
# Page I — 10 metric cards (DASH-03 / SC2)
# -----------------------------------------------------------------------------


# Conventional combined-score thresholds for the LONG / SHORT side counts.
# Phase 5 will refine these via portfolio config; v1 uses fixed cutoffs that
# match the percentile-rank semantics (top quintile / bottom quintile).
LONG_THRESHOLD = 80.0
SHORT_THRESHOLD = 20.0
INSIDER_WINDOW_DAYS = 30
CLUSTER_BUY_MIN_INSIDERS = 3
CROWDING_MIN_FUNDS = 3
EARNINGS_LOOKAHEAD_DAYS = 7


def long_candidate_count(
    conn: sqlite3.Connection, asof: date, *, threshold: float = LONG_THRESHOLD
) -> int:
    """Tickers with combined parent_score >= threshold (top quintile)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM factor_scores_parent "
        "WHERE score_date = ? AND factor = 'combined' AND parent_score >= ?",
        (asof.isoformat(), float(threshold)),
    ).fetchone()
    return int(row[0]) if row else 0


def short_candidate_count(
    conn: sqlite3.Connection, asof: date, *, threshold: float = SHORT_THRESHOLD
) -> int:
    """Tickers with combined parent_score <= threshold (bottom quintile)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM factor_scores_parent "
        "WHERE score_date = ? AND factor = 'combined' AND parent_score <= ?",
        (asof.isoformat(), float(threshold)),
    ).fetchone()
    return int(row[0]) if row else 0


def position_count(conn: sqlite3.Connection) -> int:
    """Open positions — Phase 5 ships ``portfolio_positions``; until then 0.

    The query is wrapped in try/except so the dashboard does not crash before
    Phase 5 lands the table (defense-in-depth: a missing table just means
    ``0`` on the metric card).
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM portfolio_positions WHERE shares != 0").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def crowding_count(conn: sqlite3.Connection) -> int:
    """Tickers where >= ``CROWDING_MIN_FUNDS`` tracked funds opened a NEW
    position at the most recent 13F period (DATA-07 multi-fund-opening flag).
    """
    try:
        row = conn.execute(
            f"""
            WITH latest AS (
              SELECT MAX(period_end) AS pe FROM institutional_holdings
            )
            SELECT COUNT(*) FROM (
              SELECT ticker, COUNT(DISTINCT cik) AS n_new
              FROM institutional_holdings, latest
              WHERE period_end = latest.pe AND is_new_position = 1
              GROUP BY ticker
              HAVING n_new >= {CROWDING_MIN_FUNDS}
            )
            """
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def insider_event_count(
    conn: sqlite3.Connection, asof: date, *, days: int = INSIDER_WINDOW_DAYS
) -> int:
    """Form 4 P-purchases in the last ``days`` (CP3 — P-only)."""
    start = (asof.fromordinal(asof.toordinal() - days)).isoformat()
    end = asof.isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) FROM insider_transactions
        WHERE transaction_code = 'P' AND transaction_date BETWEEN ? AND ?
        """,
        (start, end),
    ).fetchone()
    return int(row[0]) if row else 0


def ceo_buy_count(conn: sqlite3.Connection, asof: date, *, days: int = INSIDER_WINDOW_DAYS) -> int:
    """P-purchases where insider title indicates CEO/CFO (officer rank)."""
    start = (asof.fromordinal(asof.toordinal() - days)).isoformat()
    end = asof.isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) FROM insider_transactions
        WHERE transaction_code = 'P'
          AND transaction_date BETWEEN ? AND ?
          AND (
            UPPER(COALESCE(insider_title, '')) LIKE '%CEO%' OR
            UPPER(COALESCE(insider_title, '')) LIKE '%CHIEF EXECUTIVE%' OR
            UPPER(COALESCE(insider_title, '')) LIKE '%CFO%' OR
            UPPER(COALESCE(insider_title, '')) LIKE '%CHIEF FINANCIAL%'
          )
        """,
        (start, end),
    ).fetchone()
    return int(row[0]) if row else 0


def cluster_buy_count(
    conn: sqlite3.Connection,
    asof: date,
    *,
    days: int = INSIDER_WINDOW_DAYS,
    min_distinct_insiders: int = CLUSTER_BUY_MIN_INSIDERS,
) -> int:
    """Tickers with >=3 distinct insiders P-purchasing in the last 30 days
    (the canonical "cluster buy" definition)."""
    start = (asof.fromordinal(asof.toordinal() - days)).isoformat()
    end = asof.isoformat()
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
          SELECT ticker, COUNT(DISTINCT insider_name) AS n_insiders
          FROM insider_transactions
          WHERE transaction_code = 'P' AND transaction_date BETWEEN ? AND ?
          GROUP BY ticker
          HAVING n_insiders >= {int(min_distinct_insiders)}
        )
        """,
        (start, end),
    ).fetchone()
    return int(row[0]) if row else 0


def vix_close(conn: sqlite3.Connection, asof: date) -> float | None:
    """Most recent ^VIX close at or before asof from ``daily_prices``."""
    row = conn.execute(
        """
        SELECT close FROM daily_prices
        WHERE ticker = '^VIX' AND date <= ?
        ORDER BY date DESC LIMIT 1
        """,
        (asof.isoformat(),),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def vix_regime(vix: float | None) -> tuple[str, str]:
    """Return ``(label, color_token)`` for the VIX-regime status badge.

    Standard buckets: <15 calm, 15-25 normal, 25-35 elevated, >35 crisis.
    """
    if vix is None:
        return ("UNKNOWN", "muted")
    if vix < 15.0:
        return ("CALM", "long")
    if vix < 25.0:
        return ("NORMAL", "muted")
    if vix < 35.0:
        return ("ELEVATED", "warn")
    return ("CRISIS", "short")


def earnings_in_n_days(
    conn: sqlite3.Connection, asof: date, *, days: int = EARNINGS_LOOKAHEAD_DAYS
) -> int:
    """Universe tickers with an earnings date in the next ``days``."""
    end = asof.fromordinal(asof.toordinal() + days).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) FROM earnings_calendar WHERE expected_date BETWEEN ? AND ?",
        (asof.isoformat(), end),
    ).fetchone()
    return int(row[0]) if row else 0


# -----------------------------------------------------------------------------
# Page II — Research (DASH-04 / SC3)
# -----------------------------------------------------------------------------


def crowding_warnings(conn: sqlite3.Connection, *, limit: int = 20) -> pd.DataFrame:
    """Tickers crowded by tracked funds at the latest period_end.

    Returns columns: ticker, n_new_funds, n_total_funds. Sorted by n_new_funds DESC.
    """
    try:
        sql = f"""
        WITH latest AS (
          SELECT MAX(period_end) AS pe FROM institutional_holdings
        )
        SELECT
          ticker,
          SUM(is_new_position) AS n_new_funds,
          COUNT(DISTINCT cik) AS n_total_funds
        FROM institutional_holdings, latest
        WHERE period_end = latest.pe
        GROUP BY ticker
        HAVING n_new_funds >= {CROWDING_MIN_FUNDS}
        ORDER BY n_new_funds DESC
        LIMIT ?
        """
        return pd.read_sql_query(sql, conn, params=(int(limit),))
    except sqlite3.OperationalError:
        return pd.DataFrame(columns=["ticker", "n_new_funds", "n_total_funds"])


def factor_heatmap(
    conn: sqlite3.Connection, asof: date, *, top: int = 30, bottom: int = 30
) -> pd.DataFrame:
    """Top-N + Bottom-N tickers by combined parent_score × 8 base factors.

    Returns wide-form: ticker (index), 8 base-factor columns + 'combined'.
    Tickers ordered top-down: best longs first, then worst (short candidates)
    so the rendered heatmap reads "best at top, worst at bottom".
    """
    longs = top_candidates(conn, asof, top=top)
    if longs.empty:
        return pd.DataFrame(columns=["ticker", *BASE_FACTORS, "combined"])

    # Worst-N: same SQL but ASC order
    sql = """
        SELECT ticker, sector, parent_score AS combined_score, n_subfactors_used
        FROM factor_scores_parent
        WHERE score_date = ? AND factor = 'combined'
        ORDER BY parent_score ASC NULLS LAST
        LIMIT ?
    """
    shorts = pd.read_sql_query(sql, conn, params=(asof.isoformat(), int(bottom)))
    if not shorts.empty:
        shorts.insert(0, "rank", range(1, len(shorts) + 1))

    combined_tickers = [*longs["ticker"].tolist(), *shorts["ticker"].tolist()]
    return factor_breakdown(conn, asof, combined_tickers)


def candidate_cards(
    conn: sqlite3.Connection,
    asof: date,
    *,
    side: str,
    n: int = 10,
) -> pd.DataFrame:
    """Top-N long or bottom-N short candidates with Piotroski + Altman raw values.

    Side = 'long' → DESC by combined_score; 'short' → ASC.
    Pulls ``qual_piotroski_f`` and ``qual_altman_z`` raw_value sub_factors
    from ``factor_scores`` (Phase 2 quality module produces both).
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short'; got {side!r}")
    order = "DESC" if side == "long" else "ASC"

    base_sql = f"""
        SELECT ticker, sector, parent_score AS combined_score
        FROM factor_scores_parent
        WHERE score_date = ? AND factor = 'combined'
        ORDER BY parent_score {order} NULLS LAST
        LIMIT ?
    """
    base = pd.read_sql_query(base_sql, conn, params=(asof.isoformat(), int(n)))
    if base.empty:
        return base.assign(piotroski_f=pd.NA, altman_z=pd.NA, altman_zone="")

    placeholders = ",".join("?" * len(base["ticker"]))
    qual_sql = f"""
        SELECT ticker, sub_factor, raw_value
        FROM factor_scores
        WHERE score_date = ? AND factor = 'quality'
          AND sub_factor IN ('qual_piotroski_f', 'qual_altman_z')
          AND ticker IN ({placeholders})
    """
    params: list[object] = [asof.isoformat(), *base["ticker"].tolist()]
    qual = pd.read_sql_query(qual_sql, conn, params=params)
    if qual.empty:
        return base.assign(piotroski_f=pd.NA, altman_z=pd.NA, altman_zone="")

    wide = qual.pivot_table(
        index="ticker", columns="sub_factor", values="raw_value", aggfunc="first"
    ).reset_index()
    out = base.merge(wide, on="ticker", how="left")
    out = out.rename(columns={"qual_piotroski_f": "piotroski_f", "qual_altman_z": "altman_z"})
    if "piotroski_f" not in out.columns:
        out["piotroski_f"] = pd.NA
    if "altman_z" not in out.columns:
        out["altman_z"] = pd.NA
    out["altman_zone"] = out["altman_z"].map(_altman_zone_label)
    # Preserve the requested side order (the merge can shuffle).
    out["__order__"] = out["ticker"].map({t: i for i, t in enumerate(base["ticker"].tolist())})
    out = out.sort_values("__order__").drop(columns="__order__").reset_index(drop=True)
    return out[["ticker", "sector", "combined_score", "piotroski_f", "altman_z", "altman_zone"]]


def _altman_zone_label(z: float | None) -> str:
    """Original Altman Z thresholds: >2.99 safe / 1.81-2.99 grey / <1.81 distress."""
    if z is None or pd.isna(z):
        return ""
    if z > 2.99:
        return "safe"
    if z >= 1.81:
        return "grey"
    return "distress"


# -----------------------------------------------------------------------------
# Status strip / config indicators
# -----------------------------------------------------------------------------


def data_provider_label(conn: sqlite3.Connection) -> str:
    """Return a human-readable data-source label.

    Phase 1 stores no provenance metric in SQLite, so we infer from the
    ``benchmarks.last_updated`` timestamps and the existence of ``daily_prices``
    to surface "yfinance" (the only v1 implementation per DATA-14).
    """
    row = conn.execute("SELECT COUNT(*) FROM daily_prices WHERE adj_close IS NOT NULL").fetchone()
    have_prices = row and int(row[0]) > 0
    return "yfinance (v1)" if have_prices else "no data yet"


__all__ = [
    "BASE_FACTORS",
    "CLUSTER_BUY_MIN_INSIDERS",
    "CROWDING_MIN_FUNDS",
    "EARNINGS_LOOKAHEAD_DAYS",
    "INSIDER_WINDOW_DAYS",
    "LONG_THRESHOLD",
    "SHORT_THRESHOLD",
    "available_sectors",
    "candidate_cards",
    "ceo_buy_count",
    "cluster_buy_count",
    "crowding_count",
    "crowding_warnings",
    "data_provider_label",
    "earnings_in_n_days",
    "factor_breakdown",
    "factor_heatmap",
    "insider_event_count",
    "latest_score_date",
    "long_candidate_count",
    "position_count",
    "scored_size",
    "sector_distribution",
    "short_candidate_count",
    "top_candidates",
    "universe_size",
    "vix_close",
    "vix_regime",
]
