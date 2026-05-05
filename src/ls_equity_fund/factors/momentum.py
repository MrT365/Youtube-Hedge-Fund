"""SCORE-01 momentum factor.

All lookbacks are trading-day offsets against stored price rows. The module
does not rank values; it emits raw long-format rows for the Phase 2 orchestrator.

Sub-factors:
  - mom_12_1: close[asof - 21bd] / close[asof - 252bd] - 1
  - mom_6m: close[asof] / close[asof - 126bd] - 1
  - mom_3m: close[asof] / close[asof - 63bd] - 1
  - mom_accel: mom_3m - mom_6m
  - mom_52w_high: close[asof] / max(close over last 252 trading days)
  - mom_sector_rel: stock 6m gross return / sector ETF 6m gross return
"""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "mom_12_1",
    "mom_6m",
    "mom_3m",
    "mom_accel",
    "mom_52w_high",
    "mom_sector_rel",
)

# TODO: move this to config.data.sector_etfs when the Config model exposes it.
_DEFAULT_SECTOR_ETFS: dict[str, str] = {
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Financials": "XLF",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}


@register_factor("momentum")
def compute_momentum(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return columns ``ticker``, ``sub_factor``, ``raw_value`` for momentum."""
    sector_df = _load_universe_sectors(conn, tickers)
    if sector_df.empty:
        return _empty_result()

    target_tickers = sector_df["ticker"].tolist()
    sector_etfs = sorted(
        etf for etf in {_DEFAULT_SECTOR_ETFS.get(sector) for sector in sector_df["sector"]} if etf
    )
    series_by_ticker = _load_close_series(conn, [*target_tickers, *sector_etfs], asof)

    ticker_to_etf = {
        row.ticker: _DEFAULT_SECTOR_ETFS.get(row.sector)
        for row in sector_df.itertuples(index=False)
    }

    rows: list[dict[str, object]] = []
    for ticker in target_tickers:
        etf_ticker = ticker_to_etf.get(ticker)
        values = _compute_one(
            series_by_ticker.get(ticker),
            series_by_ticker.get(etf_ticker) if etf_ticker is not None else None,
        )
        rows.extend(
            {"ticker": ticker, "sub_factor": sub_factor, "raw_value": values[sub_factor]}
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows, columns=["ticker", "sub_factor", "raw_value"])
    out["raw_value"] = out["raw_value"].astype("float64")
    log.info("compute_momentum_complete", n_tickers=len(target_tickers), n_rows=len(out))
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


def _load_close_series(
    conn: sqlite3.Connection,
    tickers: list[str],
    asof: date,
) -> dict[str, pd.Series]:
    unique_tickers = sorted(set(tickers))
    if not unique_tickers:
        return {}
    placeholders = ",".join("?" * len(unique_tickers))
    prices = pd.read_sql_query(
        f"""
        SELECT ticker, date, close
        FROM daily_prices
        WHERE ticker IN ({placeholders}) AND date <= ?
        ORDER BY ticker, date
        """,
        conn,
        params=[*unique_tickers, asof.isoformat()],
    )
    out: dict[str, pd.Series] = {}
    for ticker, group in prices.groupby("ticker"):
        out[str(ticker)] = group.sort_values("date")["close"].astype("float64").reset_index(drop=True)
    return out


def _compute_one(stock: pd.Series | None, sector_etf: pd.Series | None) -> dict[str, float]:
    if stock is None or stock.empty:
        return {sub_factor: np.nan for sub_factor in SUB_FACTORS}

    c0 = _at_offset(stock, 0)
    c21 = _at_offset(stock, 21)
    c63 = _at_offset(stock, 63)
    c126 = _at_offset(stock, 126)
    c252 = _at_offset(stock, 252)

    mom_12_1 = _return(c21, c252)
    mom_6m = _return(c0, c126)
    mom_3m = _return(c0, c63)
    mom_accel = mom_3m - mom_6m if not np.isnan(mom_3m) and not np.isnan(mom_6m) else np.nan
    mom_52w_high = _proximity_to_high(stock, c0)
    mom_sector_rel = _sector_relative(c0, c126, sector_etf)

    return {
        "mom_12_1": mom_12_1,
        "mom_6m": mom_6m,
        "mom_3m": mom_3m,
        "mom_accel": mom_accel,
        "mom_52w_high": mom_52w_high,
        "mom_sector_rel": mom_sector_rel,
    }


def _at_offset(series: pd.Series, n_back: int) -> float:
    if len(series) <= n_back:
        return float("nan")
    return float(series.iloc[-1 - n_back])


def _return(numerator: float, denominator: float) -> float:
    if np.isnan(numerator) or np.isnan(denominator) or denominator == 0.0:
        return float("nan")
    return numerator / denominator - 1.0


def _proximity_to_high(series: pd.Series, current: float) -> float:
    if len(series) < 252 or np.isnan(current):
        return float("nan")
    high = float(series.iloc[-252:].max())
    if high == 0.0 or np.isnan(high):
        return float("nan")
    return current / high


def _sector_relative(current: float, six_month: float, sector_etf: pd.Series | None) -> float:
    if (
        sector_etf is None
        or np.isnan(current)
        or np.isnan(six_month)
        or six_month == 0.0
        or len(sector_etf) <= 126
    ):
        return float("nan")
    etf_current = _at_offset(sector_etf, 0)
    etf_six_month = _at_offset(sector_etf, 126)
    if np.isnan(etf_current) or np.isnan(etf_six_month) or etf_six_month == 0.0:
        return float("nan")
    etf_gross_return = etf_current / etf_six_month
    if etf_gross_return == 0.0:
        return float("nan")
    return (current / six_month) / etf_gross_return


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_momentum"]
