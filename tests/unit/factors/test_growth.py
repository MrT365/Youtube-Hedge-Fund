"""Growth factor tests for SCORE-04."""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.growth import SUB_FACTORS, compute_growth

ASOF = date(2026, 5, 4)
PRIOR_ASOF = date(2025, 5, 4)


def _insert_universe(conn: sqlite3.Connection, tickers: list[str] | None = None) -> None:
    rows = [
        (
            ticker,
            f"{ticker} Corp",
            "NYSE",
            "NYSE",
            "Information Technology",
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
    asof: date = ASOF,
    revenue_growth_yoy: float | None = 0.15,
    earnings_growth_yoy: float | None = 0.20,
    rd_intensity: float | None = 0.08,
) -> None:
    conn.execute(
        """
        INSERT INTO fundamental_ratios (
            ticker, asof_date, revenue_growth_yoy, earnings_growth_yoy, rd_intensity
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (ticker, asof.isoformat(), revenue_growth_yoy, earnings_growth_yoy, rd_intensity),
    )


def _insert_quarterly_fcf(conn: sqlite3.Connection, values: list[float], ticker: str = "T1") -> None:
    period_ends = ["2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
    conn.executemany(
        """
        INSERT INTO fundamentals (
            ticker, period_end, period_type, as_of_ingest_date, free_cash_flow
        ) VALUES (?, ?, 'quarterly', ?, ?)
        """,
        [
            (ticker, period_end, period_end, fcf)
            for period_end, fcf in zip(period_ends[: len(values)], values, strict=True)
        ],
    )


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def test_five_subfactors(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn)
    _insert_quarterly_fcf(migrated_conn, [120.0, 0.0, 0.0, 0.0, 100.0])

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 5


def test_rev_yoy_passthrough(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn, revenue_growth_yoy=0.15)

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert _value(df, "grow_rev_yoy") == pytest.approx(0.15)


def test_earn_yoy_passthrough(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn, earnings_growth_yoy=0.20)

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert _value(df, "grow_earn_yoy") == pytest.approx(0.20)


def test_rd_intensity_passthrough(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn, rd_intensity=0.08)

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert _value(df, "grow_rd_intensity") == pytest.approx(0.08)


def test_rev_accel(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn, asof=ASOF, revenue_growth_yoy=0.15)
    _insert_ratios(migrated_conn, asof=PRIOR_ASOF, revenue_growth_yoy=0.10)

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert _value(df, "grow_rev_accel") == pytest.approx(0.05)


def test_fcf_yoy_positive_prior(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_quarterly_fcf(migrated_conn, [120.0, 0.0, 0.0, 0.0, 100.0])

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert _value(df, "grow_fcf_yoy") == pytest.approx(0.20)


def test_fcf_yoy_negative_prior_uses_abs(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_quarterly_fcf(migrated_conn, [10.0, 0.0, 0.0, 0.0, -50.0])

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert _value(df, "grow_fcf_yoy") == pytest.approx(1.20)


def test_grow_rev_accel_nan_when_prior_missing(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_ratios(migrated_conn, asof=ASOF, revenue_growth_yoy=0.15)

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert np.isnan(_value(df, "grow_rev_accel"))


def test_fcf_yoy_nan_when_prior_missing(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_quarterly_fcf(migrated_conn, [120.0])

    df = compute_growth(migrated_conn, ASOF, ["T1"])

    assert np.isnan(_value(df, "grow_fcf_yoy"))


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["growth"] is compute_growth


def test_long_format_dataframe(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1", "T2"])
    _insert_ratios(migrated_conn, ticker="T1")
    _insert_ratios(migrated_conn, ticker="T2")

    df = compute_growth(migrated_conn, ASOF, ["T1", "T2"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value"]
    assert str(df["raw_value"].dtype) == "float64"
    assert df.groupby("ticker").size().to_dict() == {"T1": 5, "T2": 5}
    assert df.groupby(["ticker", "sub_factor"]).size().eq(1).all()
