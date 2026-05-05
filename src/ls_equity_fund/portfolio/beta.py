"""Rolling beta vs SPY (PORT-07).

Computes the per-stock beta from the trailing N daily returns (default 60d)
against SPY using ``cov(stock, spy) / var(spy)``. Returns ``NaN`` when there
is insufficient history or zero benchmark variance (delisted SPY rows or a
brand-new ticker with <20 returns).

Also exposes book-level beta aggregations:
  * long_book_beta   — weight-average beta across long positions only
  * short_book_beta  — weight-average across shorts (the position weights are
    negative; we take absolute weights for the average)
  * net_beta         — weighted sum across both books (signed weights). This
    is the beta the risk veto's ``|net_beta| <= 0.20`` check fires on.

All math is plain pandas — no statsmodels dependency for the v1 path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date as date_type

import numpy as np
import pandas as pd

DEFAULT_BETA_LOOKBACK = 60
MIN_OBS = 20


@dataclass(frozen=True)
class BookBeta:
    """Aggregate beta exposures (PORT-07)."""

    net_beta: float
    long_book_beta: float
    short_book_beta: float
    n_long: int
    n_short: int


def _load_returns(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    asof: date_type,
    lookback: int,
) -> pd.DataFrame:
    """Pull adj_close returns for tickers (+SPY) over the lookback window.

    Returns a DataFrame with columns=[ticker, date, ret] indexed numerically.
    Empty if no data.
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "ret"])
    universe = list({*tickers, "SPY"})
    placeholders = ",".join("?" * len(universe))
    # Pull ~2x lookback to cover trading-day vs calendar-day skew.
    horizon_days = max(lookback * 2 + 5, 90)
    start_date = (pd.Timestamp(asof) - pd.Timedelta(days=horizon_days)).date().isoformat()
    df = pd.read_sql_query(
        f"""
        SELECT ticker, date, adj_close
        FROM daily_prices
        WHERE ticker IN ({placeholders})
          AND date <= ?
          AND date >= ?
        ORDER BY ticker, date
        """,
        conn,
        params=[*universe, asof.isoformat(), start_date],
    )
    if df.empty:
        return pd.DataFrame(columns=["ticker", "date", "ret"])
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce")
    df = df.dropna(subset=["adj_close"])
    df = df.sort_values(["ticker", "date"])
    df["ret"] = df.groupby("ticker", group_keys=False)["adj_close"].pct_change()
    return df.dropna(subset=["ret"])[["ticker", "date", "ret"]]


def compute_betas(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    asof: date_type,
    lookback: int = DEFAULT_BETA_LOOKBACK,
) -> dict[str, float]:
    """Return ``{ticker: beta_vs_spy}``. NaN entries are dropped.

    Uses the most recent ``lookback`` daily returns up to and including
    ``asof``. Tickers with fewer than ``MIN_OBS`` (=20) overlapping returns
    against SPY are omitted from the output.
    """
    rets = _load_returns(conn, tickers=tickers, asof=asof, lookback=lookback)
    if rets.empty:
        return {}
    pivot = rets.pivot(index="date", columns="ticker", values="ret")
    if "SPY" not in pivot.columns:
        return {}
    spy = pivot["SPY"]
    out: dict[str, float] = {}
    for ticker in tickers:
        if ticker not in pivot.columns:
            continue
        series = pivot[ticker]
        common = pd.concat([series, spy], axis=1, keys=["t", "spy"]).dropna()
        common = common.tail(lookback)
        if len(common) < MIN_OBS:
            continue
        var_spy = float(common["spy"].var(ddof=1))
        if var_spy <= 0 or np.isnan(var_spy):
            continue
        cov = float(common["t"].cov(common["spy"]))
        beta = cov / var_spy
        if np.isnan(beta) or np.isinf(beta):
            continue
        out[ticker] = beta
    return out


def aggregate_book_beta(
    *,
    weights: pd.Series,
    betas: dict[str, float],
) -> BookBeta:
    """Aggregate per-stock beta into long-book / short-book / net beta.

    Args:
        weights: signed weights indexed by ticker. Positive = long, negative =
            short. Values that are ~0 are ignored.
        betas:   ``{ticker: beta}`` from ``compute_betas``.

    Returns:
        BookBeta with rounded floats; missing betas treated as 0 (with a soft
        warning logged at the call site).
    """
    if weights.empty:
        return BookBeta(0.0, 0.0, 0.0, 0, 0)

    longs = weights[weights > 0]
    shorts = weights[weights < 0]

    def _book(side: pd.Series) -> tuple[float, int]:
        if side.empty:
            return 0.0, 0
        b = side.index.map(lambda t: betas.get(t, np.nan))
        valid = ~pd.isna(b)
        if not valid.any():
            return 0.0, len(side)
        gross = float(side.abs().sum())
        if gross <= 0:
            return 0.0, len(side)
        beta_arr = np.array(b[valid], dtype=float)
        w_arr = side.abs().to_numpy()[valid]
        weighted = float((beta_arr * w_arr).sum() / gross)
        return weighted, len(side)

    long_beta, n_long = _book(longs)
    short_beta, n_short = _book(shorts)

    # Net beta — signed weighted sum across the entire book (gross-normalised).
    if not weights.empty:
        b_arr = weights.index.map(lambda t: betas.get(t, np.nan))
        valid = ~pd.isna(b_arr)
        if valid.any():
            beta_arr = np.array(b_arr[valid], dtype=float)
            w_arr = weights.to_numpy()[valid]
            net = float((beta_arr * w_arr).sum())
        else:
            net = 0.0
    else:
        net = 0.0

    return BookBeta(
        net_beta=net,
        long_book_beta=long_beta,
        short_book_beta=short_beta,
        n_long=n_long,
        n_short=n_short,
    )


__all__ = [
    "DEFAULT_BETA_LOOKBACK",
    "BookBeta",
    "aggregate_book_beta",
    "compute_betas",
]
