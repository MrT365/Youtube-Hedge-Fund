"""Quality factor tests for SCORE-03."""

from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.quality import SUB_FACTORS, compute_quality

ASOF = date(2026, 5, 4)


def _insert_universe(conn: sqlite3.Connection, tickers: list[str] | None = None) -> None:
    rows = [
        (
            ticker,
            f"{ticker} Corp",
            "NYSE",
            "NYSE",
            "Industrials",
            None,
            None,
            "2025-01-01",
            None,
            "active",
            1,
        )
        for ticker in (tickers or ["T1"])
    ]
    conn.executemany(
        """
        INSERT INTO universe (
            ticker, company_name, exchange, primary_listing, sector, industry,
            sub_industry, first_seen_date, delisted_date, inclusion_window, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_ratios(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    gross_margin: float = 0.45,
    debt_to_equity: float = 0.5,
    cfo_to_ni: float = 1.2,
    accruals_ratio: float = 0.10,
) -> None:
    conn.execute(
        """
        INSERT INTO fundamental_ratios (
            ticker, asof_date, gross_margin, debt_to_equity, cfo_to_ni, accruals_ratio
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticker, "2026-04-30", gross_margin, debt_to_equity, cfo_to_ni, accruals_ratio),
    )


def _insert_fundamental(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    period_end: str,
    period_type: str,
    net_income: float = 100.0,
    total_equity: float = 1_000.0,
    gross_profit: float = 500.0,
    revenue: float = 1_000.0,
    cfo: float = 120.0,
    total_assets: float = 1_000.0,
    total_liabilities: float = 400.0,
    long_term_debt: float = 100.0,
    current_assets: float = 400.0,
    current_liabilities: float = 200.0,
    shares_outstanding: float = 90.0,
    ebit: float = 150.0,
    retained_earnings: float = 250.0,
    working_capital: float = 200.0,
) -> None:
    conn.execute(
        """
        INSERT INTO fundamentals (
            ticker, period_end, period_type, as_of_ingest_date, revenue, gross_profit,
            net_income, total_assets, total_liabilities, total_equity, current_assets,
            current_liabilities, long_term_debt, cfo, shares_outstanding, ebit,
            retained_earnings, working_capital
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            period_end,
            period_type,
            "2026-05-01",
            revenue,
            gross_profit,
            net_income,
            total_assets,
            total_liabilities,
            total_equity,
            current_assets,
            current_liabilities,
            long_term_debt,
            cfo,
            shares_outstanding,
            ebit,
            retained_earnings,
            working_capital,
        ),
    )


def _seed_quality_inputs(conn: sqlite3.Connection) -> None:
    _insert_universe(conn)
    _insert_ratios(conn)
    _insert_fundamental(
        conn,
        period_end="2025-12-31",
        period_type="annual",
        gross_profit=500.0,
        revenue=1_000.0,
    )
    _insert_fundamental(
        conn,
        period_end="2024-12-31",
        period_type="annual",
        net_income=80.0,
        gross_profit=400.0,
        revenue=1_000.0,
        long_term_debt=200.0,
        current_assets=300.0,
        shares_outstanding=100.0,
    )
    for idx, period_end in enumerate(
        [
            "2024-06-30",
            "2024-09-30",
            "2024-12-31",
            "2025-03-31",
            "2025-06-30",
            "2025-09-30",
            "2025-12-31",
            "2026-03-31",
        ]
    ):
        _insert_fundamental(
            conn,
            period_end=period_end,
            period_type="quarterly",
            net_income=100.0 + idx,
            total_equity=1_000.0,
        )
    conn.execute("INSERT INTO daily_prices (ticker, date, close) VALUES (?, ?, ?)", ("T1", ASOF.isoformat(), 10.0))


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def test_eight_subfactors_emitted(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 8


def test_de_inv_sign(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_de_inv") == pytest.approx(-0.5)


def test_accruals_inv_sign(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_accruals_inv") == pytest.approx(-0.10)


def test_gm_level_passthrough(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_gm_level") == pytest.approx(0.45)


def test_gm_trend(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_gm_trend") == pytest.approx(0.10)


def test_cfo_ni_passthrough(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_cfo_ni") == pytest.approx(1.2)


def test_roe_stability(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn)
    for period_end in [
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
        "2025-03-31",
        "2025-06-30",
        "2025-09-30",
        "2025-12-31",
        "2026-03-31",
    ]:
        _insert_fundamental(
            migrated_conn,
            period_end=period_end,
            period_type="quarterly",
            net_income=100.0,
            total_equity=1_000.0,
        )

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    stability = _value(df, "qual_roe_stability")
    assert np.isfinite(stability)
    assert stability == pytest.approx(1e9)


def test_roe_stability_nan_when_less_than_8_quarters(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn)
    for period_end in ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]:
        _insert_fundamental(migrated_conn, period_end=period_end, period_type="quarterly")

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert np.isnan(_value(df, "qual_roe_stability"))


def test_piotroski_uses_helper(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    with patch("ls_equity_fund.factors.quality.compute_piotroski_f", return_value=7):
        df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_piotroski_f") == 7.0


def test_altman_z_uses_helper(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    with patch("ls_equity_fund.factors.quality.compute_altman_z", return_value=3.5):
        df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert _value(df, "qual_altman_z") == 3.5


def test_nan_when_inputs_missing(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert len(df) == 8
    assert df["raw_value"].isna().all()


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["quality"] is compute_quality


def test_long_format_dataframe(migrated_conn: sqlite3.Connection) -> None:
    _seed_quality_inputs(migrated_conn)

    df = compute_quality(migrated_conn, ASOF, ["T1"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value"]
    assert str(df["raw_value"].dtype) == "float64"
    assert len(df) == 8
