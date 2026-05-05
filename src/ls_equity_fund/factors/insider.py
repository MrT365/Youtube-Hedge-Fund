"""SCORE-07 insider activity factor.

CP3 binding is load-bearing here:
  - ``ins_net_flow_90d`` reads only P/S transaction codes.
  - ``ins_ceo_cfo_buys`` and ``ins_cluster_buy_count`` read only P codes.

A/M/F/G/D Form 4 codes are deliberately excluded from every directional query.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.data.providers.edgar_provider import CEO_CFO_TITLE_RE
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "ins_net_flow_90d",
    "ins_ceo_cfo_buys",
    "ins_cluster_buy_count",
)

NET_FLOW_WINDOW_DAYS = 90
CLUSTER_WINDOW_DAYS = 30
CEO_CFO_WEIGHT = 3.0
OUTPUT_COLUMNS = ["ticker", "sub_factor", "raw_value", "sufficient_history"]


@register_factor("insider")
def compute_insider(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return long-format insider sub-factor rows."""
    sector_df = _load_universe_sectors(conn, tickers)
    if sector_df.empty:
        return _empty_result()

    target_tickers = sector_df["ticker"].tolist()
    net_flow = _net_flow_per_ticker(conn, asof, target_tickers)
    ceo_cfo_buys = _ceo_cfo_buys_per_ticker(conn, asof, target_tickers)
    cluster_buys = _cluster_buy_count_per_ticker(conn, asof, target_tickers)

    rows: list[dict[str, object]] = []
    for ticker in target_tickers:
        has_insider_data = ticker in net_flow
        values = {
            "ins_net_flow_90d": net_flow.get(ticker, np.nan),
            "ins_ceo_cfo_buys": ceo_cfo_buys.get(ticker, 0.0 if has_insider_data else np.nan),
            "ins_cluster_buy_count": cluster_buys.get(ticker, 0.0 if has_insider_data else np.nan),
        }
        rows.extend(
            {
                "ticker": ticker,
                "sector": _sector_for_ticker(sector_df, ticker),
                "sub_factor": sub_factor,
                "raw_value": values[sub_factor],
                "sufficient_history": 1,
            }
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows)
    out = _apply_sector_median_fallback(out)
    out = out[OUTPUT_COLUMNS]
    out["raw_value"] = out["raw_value"].astype("float64")
    out["sufficient_history"] = out["sufficient_history"].astype("int64")
    log.info("compute_insider_complete", n_tickers=len(target_tickers), n_rows=len(out))
    return out


def _load_universe_sectors(conn: sqlite3.Connection, tickers: list[str] | None) -> pd.DataFrame:
    query = "SELECT ticker, sector FROM universe"
    params: list[str] = []
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        query += f" WHERE ticker IN ({placeholders})"
        params.extend(tickers)
    query += " ORDER BY ticker"
    return pd.read_sql_query(query, conn, params=params)


def _net_flow_per_ticker(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str],
) -> dict[str, float]:
    """Return SUM(P) - SUM(S) over the 90-day window."""
    if not tickers:
        return {}
    start = (asof - timedelta(days=NET_FLOW_WINDOW_DAYS)).isoformat()
    end = asof.isoformat()
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker,
               SUM(CASE
                   WHEN transaction_code = 'P' THEN COALESCE(total_value, 0)
                   WHEN transaction_code = 'S' THEN -COALESCE(total_value, 0)
                   ELSE 0
               END) AS net_flow
        FROM insider_transactions WHERE transaction_code IN ('P','S')
          AND ticker IN ({placeholders})
          AND transaction_date BETWEEN ? AND ?
        GROUP BY ticker
        """,
        [*tickers, start, end],
    ).fetchall()
    return {str(row[0]): float(row[1]) for row in rows if row[1] is not None}


def _ceo_cfo_buys_per_ticker(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str],
) -> dict[str, float]:
    """Return 3x weighted CEO/CFO P-code purchases over the 90-day window."""
    if not tickers:
        return {}
    start = (asof - timedelta(days=NET_FLOW_WINDOW_DAYS)).isoformat()
    end = asof.isoformat()
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, insider_title, total_value
        FROM insider_transactions WHERE transaction_code = 'P'
          AND is_officer = 1
          AND ticker IN ({placeholders})
          AND transaction_date BETWEEN ? AND ?
        """,
        [*tickers, start, end],
    ).fetchall()

    out: dict[str, float] = {}
    for ticker, insider_title, total_value in rows:
        if CEO_CFO_TITLE_RE.search(insider_title or ""):
            out[str(ticker)] = out.get(str(ticker), 0.0) + CEO_CFO_WEIGHT * float(total_value or 0.0)
    return out


def _cluster_buy_count_per_ticker(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str],
) -> dict[str, float]:
    """Return distinct P-code insider count over the 30-day cluster window."""
    if not tickers:
        return {}
    start = (asof - timedelta(days=CLUSTER_WINDOW_DAYS)).isoformat()
    end = asof.isoformat()
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, COUNT(DISTINCT insider_name) AS cluster_buy_count
        FROM insider_transactions WHERE transaction_code = 'P'
          AND ticker IN ({placeholders})
          AND transaction_date BETWEEN ? AND ?
        GROUP BY ticker
        """,
        [*tickers, start, end],
    ).fetchall()
    return {str(row[0]): float(row[1]) for row in rows}


def _apply_sector_median_fallback(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    raw_wide = out.pivot(index="ticker", columns="sub_factor", values="raw_value")
    no_data_tickers = set(raw_wide.index[raw_wide.isna().all(axis=1)])
    if not no_data_tickers:
        return out

    for index, row in out.iterrows():
        if row["ticker"] not in no_data_tickers:
            continue
        median = out.loc[
            (out["sector"] == row["sector"])
            & (out["sub_factor"] == row["sub_factor"])
            & (~out["ticker"].isin(no_data_tickers)),
            "raw_value",
        ].median(skipna=True)
        out.at[index, "raw_value"] = float(median) if not pd.isna(median) else np.nan
        out.at[index, "sufficient_history"] = 0
    return out


def _sector_for_ticker(sector_df: pd.DataFrame, ticker: str) -> object:
    return sector_df.loc[sector_df["ticker"] == ticker, "sector"].iloc[0]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
            "sufficient_history": pd.Series(dtype="int64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_insider"]
