"""Barra-style factor risk model (RISK-01, RISK-02).

The model uses stored Phase 2 parent factor scores as cross-sectional
exposures, estimates daily factor returns with ordinary least squares over a
120-trading-day window, and applies Ledoit-Wolf shrinkage to the annualized
factor covariance matrix. The predicted stock covariance matrix is consumable
by Phase 7 MVO.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from ls_equity_fund.portfolio.factor_exposure import BASE_FACTORS

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class FactorRiskResult:
    """Full output of the Phase 6 factor risk model."""

    factor_returns: pd.DataFrame
    factor_covariance: pd.DataFrame
    specific_variance: pd.Series
    predicted_covariance: pd.DataFrame
    portfolio_factor_variance: float
    portfolio_specific_variance: float
    portfolio_total_variance: float
    mctr: pd.Series
    used_ledoit_wolf: bool


def compute_factor_risk_model(
    conn: sqlite3.Connection,
    *,
    asof: date,
    weights: pd.Series,
    lookback: int = 120,
    factors: tuple[str, ...] = BASE_FACTORS,
) -> FactorRiskResult:
    """Estimate factor covariance, specific variance, predicted covariance and MCTR.

    Args:
        conn: SQLite connection.
        asof: score/risk date.
        weights: signed portfolio weights indexed by ticker.
        lookback: trailing daily return count.
        factors: parent factors to use as exposures.
    """
    tickers = list(weights.index)
    exposures = load_factor_exposures(conn, tickers=tickers, asof=asof, factors=factors)
    returns = load_stock_returns(conn, tickers=tickers, asof=asof, lookback=lookback)
    common_tickers = [t for t in tickers if t in exposures.index and t in returns.columns]
    if not common_tickers:
        empty = pd.DataFrame()
        return FactorRiskResult(
            factor_returns=empty,
            factor_covariance=empty,
            specific_variance=pd.Series(dtype=float),
            predicted_covariance=empty,
            portfolio_factor_variance=0.0,
            portfolio_specific_variance=0.0,
            portfolio_total_variance=0.0,
            mctr=pd.Series(dtype=float),
            used_ledoit_wolf=False,
        )

    exposures = exposures.loc[common_tickers].astype(float)
    # Factor scores are 0-100; center and scale so regression intercept is not
    # forced to absorb the market level.
    exposures = (exposures - 50.0) / 50.0
    returns = returns[common_tickers].dropna(how="all").tail(lookback)

    factor_returns_rows: list[pd.Series] = []
    residual_rows: list[pd.Series] = []
    x = exposures.to_numpy(dtype=float)
    x = np.nan_to_num(x, nan=0.0)
    x_design = np.column_stack([np.ones(len(common_tickers)), x])
    for dt, row in returns.iterrows():
        y = row.to_numpy(dtype=float)
        valid = ~np.isnan(y)
        if int(valid.sum()) <= len(factors):
            continue
        beta, *_ = np.linalg.lstsq(x_design[valid], y[valid], rcond=None)
        fitted = x_design @ beta
        factor_returns_rows.append(pd.Series(beta[1:], index=factors, name=dt))
        residual_rows.append(pd.Series(y - fitted, index=common_tickers, name=dt))

    factor_returns = pd.DataFrame(factor_returns_rows)
    residuals = pd.DataFrame(residual_rows)
    if factor_returns.empty:
        factor_cov = pd.DataFrame(np.eye(len(factors)) * 1e-8, index=factors, columns=factors)
        used_lw = False
    else:
        factor_cov, used_lw = ledoit_wolf_covariance(factor_returns)

    specific_variance = residuals.var(ddof=1).fillna(0.0) * TRADING_DAYS_PER_YEAR
    specific_variance = specific_variance.reindex(common_tickers).fillna(0.0)
    predicted_cov = predicted_covariance_from_components(exposures, factor_cov, specific_variance)

    aligned_weights = weights.reindex(common_tickers).fillna(0.0).astype(float)
    portfolio_factor_variance, portfolio_specific_variance, total_variance, mctr = (
        portfolio_risk_contribution(
            weights=aligned_weights,
            exposures=exposures,
            factor_covariance=factor_cov,
            specific_variance=specific_variance,
        )
    )
    return FactorRiskResult(
        factor_returns=factor_returns,
        factor_covariance=factor_cov,
        specific_variance=specific_variance,
        predicted_covariance=predicted_cov,
        portfolio_factor_variance=portfolio_factor_variance,
        portfolio_specific_variance=portfolio_specific_variance,
        portfolio_total_variance=total_variance,
        mctr=mctr,
        used_ledoit_wolf=used_lw,
    )


def ledoit_wolf_covariance(factor_returns: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Return annualized Ledoit-Wolf shrunk covariance of factor returns."""
    clean = factor_returns.dropna(how="any")
    if clean.empty:
        factors = list(factor_returns.columns)
        return pd.DataFrame(np.eye(len(factors)) * 1e-8, index=factors, columns=factors), False
    lw = LedoitWolf()
    lw.fit(clean.to_numpy(dtype=float))
    cov = lw.covariance_ * TRADING_DAYS_PER_YEAR
    return pd.DataFrame(cov, index=clean.columns, columns=clean.columns), True


def predicted_covariance_from_components(
    exposures: pd.DataFrame,
    factor_covariance: pd.DataFrame,
    specific_variance: pd.Series,
) -> pd.DataFrame:
    """Build stock covariance ``B F B' + D``."""
    factor_covariance = factor_covariance.reindex(index=exposures.columns, columns=exposures.columns).fillna(0.0)
    b = exposures.to_numpy(dtype=float)
    f = factor_covariance.to_numpy(dtype=float)
    d = np.diag(specific_variance.reindex(exposures.index).fillna(0.0).to_numpy(dtype=float))
    cov = b @ f @ b.T + d
    return pd.DataFrame(cov, index=exposures.index, columns=exposures.index)


def portfolio_risk_contribution(
    *,
    weights: pd.Series,
    exposures: pd.DataFrame,
    factor_covariance: pd.DataFrame,
    specific_variance: pd.Series,
) -> tuple[float, float, float, pd.Series]:
    """Return factor variance, specific variance, total variance, and MCTR."""
    exposures = exposures.reindex(weights.index).fillna(0.0)
    factor_covariance = factor_covariance.reindex(
        index=exposures.columns,
        columns=exposures.columns,
    ).fillna(0.0)
    specific_variance = specific_variance.reindex(weights.index).fillna(0.0)
    w = weights.to_numpy(dtype=float)
    b = exposures.to_numpy(dtype=float)
    f = factor_covariance.to_numpy(dtype=float)
    d = np.diag(specific_variance.to_numpy(dtype=float))
    factor_cov_stock = b @ f @ b.T
    factor_var = float(w.T @ factor_cov_stock @ w)
    specific_var = float(w.T @ d @ w)
    total_var = max(factor_var + specific_var, 0.0)
    pred_cov = factor_cov_stock + d
    port_vol = float(np.sqrt(total_var)) if total_var > 0 else 0.0
    if port_vol == 0:
        mctr = pd.Series(0.0, index=weights.index)
    else:
        mctr = pd.Series((pred_cov @ w) / port_vol, index=weights.index)
    return factor_var, specific_var, total_var, mctr


def load_factor_exposures(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    asof: date,
    factors: tuple[str, ...] = BASE_FACTORS,
) -> pd.DataFrame:
    """Load one row per ticker, one column per parent factor score."""
    if not tickers:
        return pd.DataFrame(columns=factors)
    placeholders = ",".join("?" * len(tickers))
    factor_placeholders = ",".join("?" * len(factors))
    df = pd.read_sql_query(
        f"""
        SELECT ticker, factor, parent_score
        FROM factor_scores_parent
        WHERE score_date = ?
          AND ticker IN ({placeholders})
          AND factor IN ({factor_placeholders})
        """,
        conn,
        params=[asof.isoformat(), *tickers, *factors],
    )
    if df.empty:
        return pd.DataFrame(columns=factors)
    pivot = df.pivot(index="ticker", columns="factor", values="parent_score")
    return pivot.reindex(index=tickers, columns=factors)


def load_stock_returns(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    asof: date,
    lookback: int,
) -> pd.DataFrame:
    """Load trailing stock returns pivoted by date/ticker."""
    if not tickers:
        return pd.DataFrame()
    placeholders = ",".join("?" * len(tickers))
    start = (pd.Timestamp(asof) - pd.Timedelta(days=max(lookback * 3, 180))).date().isoformat()
    df = pd.read_sql_query(
        f"""
        SELECT ticker, date, COALESCE(adj_close, close) AS px
        FROM daily_prices
        WHERE ticker IN ({placeholders})
          AND date <= ?
          AND date >= ?
        ORDER BY ticker, date
        """,
        conn,
        params=[*tickers, asof.isoformat(), start],
    )
    if df.empty:
        return pd.DataFrame()
    df["px"] = pd.to_numeric(df["px"], errors="coerce")
    df = df.dropna(subset=["px"]).sort_values(["ticker", "date"])
    df["ret"] = df.groupby("ticker", group_keys=False)["px"].pct_change()
    pivot = df.dropna(subset=["ret"]).pivot(index="date", columns="ticker", values="ret")
    return pivot.tail(lookback)


def write_risk_snapshots(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    result: FactorRiskResult,
) -> int:
    """Persist per-ticker risk snapshot rows."""
    if result.predicted_covariance.empty:
        return 0
    timestamp = int(time.time())
    rows = []
    diag_total = pd.Series(
        np.diag(result.predicted_covariance.to_numpy(dtype=float)),
        index=result.predicted_covariance.index,
    )
    factor_diag = (diag_total - result.specific_variance.reindex(diag_total.index).fillna(0.0)).clip(lower=0.0)
    for ticker in result.predicted_covariance.index:
        rows.append(
            (
                run_id,
                ticker,
                float(factor_diag.get(ticker, 0.0)),
                float(result.specific_variance.get(ticker, 0.0)),
                float(diag_total.get(ticker, 0.0)),
                float(result.mctr.get(ticker, 0.0)),
                timestamp,
            )
        )
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO risk_snapshots (
                run_id, ticker, factor_variance, specific_variance,
                total_variance, mctr, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


__all__ = [
    "FactorRiskResult",
    "compute_factor_risk_model",
    "ledoit_wolf_covariance",
    "load_factor_exposures",
    "load_stock_returns",
    "portfolio_risk_contribution",
    "predicted_covariance_from_components",
    "write_risk_snapshots",
]
