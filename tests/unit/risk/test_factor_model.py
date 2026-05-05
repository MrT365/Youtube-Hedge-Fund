"""Factor risk model tests."""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd

from ls_equity_fund.portfolio.factor_exposure import BASE_FACTORS
from ls_equity_fund.risk.factor_model import (
    compute_factor_risk_model,
    ledoit_wolf_covariance,
    portfolio_risk_contribution,
)


def test_ledoit_wolf_shrinkage_applied() -> None:
    returns = pd.DataFrame(
        {
            "momentum": [0.01, 0.02, -0.01, 0.00, 0.03],
            "value": [0.00, -0.01, 0.02, 0.01, -0.02],
        }
    )

    cov, used = ledoit_wolf_covariance(returns)

    assert used is True
    assert cov.shape == (2, 2)
    assert np.all(np.linalg.eigvalsh(cov.to_numpy()) >= -1e-12)


def test_mctr_calculation() -> None:
    weights = pd.Series({"A": 0.10, "B": -0.10})
    exposures = pd.DataFrame({"momentum": [1.0, -1.0]}, index=["A", "B"])
    factor_cov = pd.DataFrame([[0.04]], index=["momentum"], columns=["momentum"])
    specific = pd.Series({"A": 0.01, "B": 0.01})

    factor_var, specific_var, total_var, mctr = portfolio_risk_contribution(
        weights=weights,
        exposures=exposures,
        factor_covariance=factor_cov,
        specific_variance=specific,
    )

    assert factor_var > 0
    assert specific_var > 0
    assert total_var == factor_var + specific_var
    assert set(mctr.index) == {"A", "B"}


def test_compute_factor_risk_model_smoke(migrated_conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 4)
    tickers = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
    for ticker_idx, ticker in enumerate(tickers):
        migrated_conn.execute(
            """
            INSERT INTO universe (
                ticker, first_seen_date, inclusion_window, last_updated
            ) VALUES (?, '2025-01-01', 'active', 1)
            """,
            (ticker,),
        )
        for factor_idx, factor in enumerate(BASE_FACTORS):
            migrated_conn.execute(
                """
                INSERT INTO factor_scores_parent (
                    ticker, score_date, factor, parent_score, sector,
                    n_subfactors_used, computed_at
                ) VALUES (?, ?, ?, ?, 'Tech', 1, 1)
                """,
                (ticker, asof.isoformat(), factor, 45 + ticker_idx + factor_idx),
            )
        prices = [100 + ticker_idx + i * (1 + ticker_idx * 0.01) for i in range(180)]
        dates = pd.bdate_range(end=pd.Timestamp(asof), periods=180)
        migrated_conn.executemany(
            "INSERT INTO daily_prices (ticker, date, close, adj_close) VALUES (?, ?, ?, ?)",
            [(ticker, d.date().isoformat(), p, p) for d, p in zip(dates, prices, strict=True)],
        )

    weights = pd.Series({ticker: (0.02 if i % 2 == 0 else -0.02) for i, ticker in enumerate(tickers)})
    result = compute_factor_risk_model(migrated_conn, asof=asof, weights=weights, lookback=120)

    assert result.used_ledoit_wolf is True
    assert result.predicted_covariance.shape[0] == len(tickers)
    assert set(result.mctr.index) == set(tickers)
