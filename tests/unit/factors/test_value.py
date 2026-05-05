"""Value factor tests for SCORE-02."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.value import SUB_FACTORS, compute_value

ASOF = date(2026, 5, 4)


def _insert_universe(conn: sqlite3.Connection, ticker: str = "T1") -> None:
    conn.execute(
        """
        INSERT INTO universe (
            ticker, company_name, exchange, primary_listing, sector, industry,
            sub_industry, first_seen_date, delisted_date, inclusion_window, last_updated
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
        ),
    )


def _insert_fundamentals(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    period_end: str = "2025-12-31",
    as_of_ingest_date: str = "2026-02-15",
    revenue: float = 4400.0,
    total_equity: float = 1000.0,
    long_term_debt: float | None = 500.0,
    cash_and_equivalents: float = 300.0,
    shares_outstanding: float = 100.0,
    ebit: float = 440.0,
) -> None:
    conn.execute(
        """
        INSERT INTO fundamentals (
            ticker, period_end, period_type, as_of_ingest_date, revenue,
            total_equity, long_term_debt, cash_and_equivalents, shares_outstanding, ebit
        ) VALUES (?, ?, 'annual', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            period_end,
            as_of_ingest_date,
            revenue,
            total_equity,
            long_term_debt,
            cash_and_equivalents,
            shares_outstanding,
            ebit,
        ),
    )


def _insert_price(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    price_date: str = "2026-05-01",
    close: float = 20.0,
) -> None:
    conn.execute(
        "INSERT INTO daily_prices (ticker, date, close, adj_close) VALUES (?, ?, ?, ?)",
        (ticker, price_date, close, close),
    )


def _insert_estimate(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    snapshot_date: str = "2026-05-01",
    eps_fy1: float = 5.0,
) -> None:
    conn.execute(
        "INSERT INTO analyst_estimates (ticker, snapshot_date, eps_fy1) VALUES (?, ?, ?)",
        (ticker, snapshot_date, eps_fy1),
    )


def _insert_ratios(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    asof_date: str = "2026-05-01",
    fcf_yield: float = 0.08,
    dividend_yield: float = 0.03,
    buyback_yield: float = 0.04,
) -> None:
    conn.execute(
        """
        INSERT INTO fundamental_ratios (
            ticker, asof_date, fcf_yield, dividend_yield, buyback_yield
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (ticker, asof_date, fcf_yield, dividend_yield, buyback_yield),
    )


def _insert_complete_ticker(
    conn: sqlite3.Connection,
    *,
    ticker: str = "T1",
    close: float = 20.0,
    long_term_debt: float | None = 500.0,
) -> None:
    _insert_universe(conn, ticker)
    _insert_fundamentals(conn, ticker=ticker, long_term_debt=long_term_debt)
    _insert_price(conn, ticker=ticker, close=close)
    _insert_estimate(conn, ticker=ticker)
    _insert_ratios(conn, ticker=ticker)


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def test_six_subfactors_emitted(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 6


def test_fwd_ey_pit_correct(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_fundamentals(migrated_conn)
    _insert_price(migrated_conn, price_date="2026-04-01", close=100.0)
    _insert_price(migrated_conn, price_date="2026-05-10", close=50.0)
    _insert_estimate(migrated_conn, snapshot_date="2026-04-01", eps_fy1=5.0)
    _insert_estimate(migrated_conn, snapshot_date="2026-05-10", eps_fy1=10.0)
    _insert_ratios(migrated_conn)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert _value(df, "val_fwd_ey") == pytest.approx(0.05)


def test_fwd_ey_nan_when_no_estimates(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn)
    _insert_fundamentals(migrated_conn)
    _insert_price(migrated_conn, close=100.0)
    _insert_ratios(migrated_conn)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert np.isnan(_value(df, "val_fwd_ey"))


def test_bp_book_to_price(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn, close=10.0)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert _value(df, "val_bp") == pytest.approx(1.0)


def test_fcf_yield_passthrough(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert _value(df, "val_fcf_yield") == pytest.approx(0.08)


def test_ev_ebit_inv_proxy(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn, close=20.0)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert _value(df, "val_ev_ebit_inv") == pytest.approx(0.20)


def test_shareholder_yield(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert _value(df, "val_shareholder_yield") == pytest.approx(0.07)


def test_sales_ev(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn, close=20.0)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert _value(df, "val_sales_ev") == pytest.approx(2.0)


def test_nan_propagates_when_inputs_missing(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn, close=20.0, long_term_debt=None)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert np.isnan(_value(df, "val_ev_ebit_inv"))


def test_audit_subfactor_name_uses_ev_ebit_not_other_proxy() -> None:
    source = Path("src/ls_equity_fund/factors/value.py").read_text(encoding="utf-8")
    assert "val_ev_ebit_inv" in source
    assert "val_ev_ebitda_inv" not in source


def test_pit_correct_uses_helper() -> None:
    source = Path("src/ls_equity_fund/factors/value.py").read_text(encoding="utf-8")
    assert "from ls_equity_fund.factors._pit import" in source
    assert "as_of_ingest_date" not in source


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["value"] is compute_value


def test_long_format_dataframe(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn)

    df = compute_value(migrated_conn, ASOF, ["T1"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value"]
    assert str(df["raw_value"].dtype) == "float64"
    assert len(df) == 6


def test_multiple_tickers(migrated_conn: sqlite3.Connection) -> None:
    _insert_complete_ticker(migrated_conn, ticker="T1")
    _insert_complete_ticker(migrated_conn, ticker="T2")

    df = compute_value(migrated_conn, ASOF, ["T1", "T2"])

    assert len(df) == 12
    assert df.groupby(["ticker", "sub_factor"]).size().eq(1).all()
