"""Institutional factor tests for SCORE-08."""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.institutional import SUB_FACTORS, compute_institutional

ASOF = date(2026, 7, 1)


def _insert_universe(conn: sqlite3.Connection, tickers: list[str]) -> None:
    conn.executemany(
        """
        INSERT INTO universe (
            ticker, company_name, exchange, primary_listing, sector, industry,
            sub_industry, first_seen_date, delisted_date, inclusion_window, last_updated
        ) VALUES (?, ?, 'NYSE', 'NYSE', 'Tech', NULL, NULL, '2025-01-01', NULL, 'active', 1)
        """,
        [(ticker, f"{ticker} Corp") for ticker in tickers],
    )


def _insert_holding(
    conn: sqlite3.Connection,
    *,
    cik: str,
    ticker: str = "AAPL",
    period_end: str = "2026-03-31",
    filed_date: str = "2026-05-15",
    shares: float = 100.0,
    value_usd: float = 10_000.0,
    change_shares: float = 0.0,
    is_new_position: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO institutional_holdings (
            cik, fund_name, ticker, period_end, filed_date, shares, value_usd,
            change_shares, is_new_position
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cik,
            f"Fund {cik}",
            ticker,
            period_end,
            filed_date,
            shares,
            value_usd,
            change_shares,
            is_new_position,
        ),
    )


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "AAPL") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def test_three_subfactors_emitted(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    _insert_holding(migrated_conn, cik="A")

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 3


def test_pit_correct_period_end(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    _insert_holding(
        migrated_conn,
        cik="A",
        period_end="2026-06-30",
        filed_date="2026-08-14",
        shares=1000,
    )
    _insert_holding(
        migrated_conn,
        cik="B",
        period_end="2026-03-31",
        filed_date="2026-05-15",
        shares=500,
    )

    july = compute_institutional(migrated_conn, date(2026, 7, 1), ["AAPL"])
    sept = compute_institutional(migrated_conn, date(2026, 9, 1), ["AAPL"])

    assert _value(july, "inst_fund_count") == 1.0
    assert _value(sept, "inst_fund_count") == 1.0


def test_fund_count_distinct_cik(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    for cik in ["A", "B", "C"]:
        _insert_holding(migrated_conn, cik=cik)

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "inst_fund_count") == 3.0


def test_net_change(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    _insert_holding(migrated_conn, cik="A", shares=100, value_usd=10_000, change_shares=100)
    _insert_holding(migrated_conn, cik="B", shares=50, value_usd=5_000, change_shares=-50)

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "inst_net_change") == pytest.approx(5_000.0)


def test_multi_fund_open_flag_3plus(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    for cik in ["A", "B", "C"]:
        _insert_holding(migrated_conn, cik=cik, filed_date="2026-06-01", is_new_position=1)

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "inst_multi_fund_open_flag") == 1.0


def test_multi_fund_open_flag_below_threshold(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    for cik in ["A", "B"]:
        _insert_holding(migrated_conn, cik=cik, filed_date="2026-06-01", is_new_position=1)

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "inst_multi_fund_open_flag") == 0.0


def test_multi_fund_open_flag_outside_window(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    for cik in ["A", "B", "C", "D", "E"]:
        _insert_holding(migrated_conn, cik=cik, filed_date="2026-03-31", is_new_position=1)

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "inst_multi_fund_open_flag") == 0.0


def test_nan_when_no_holdings(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert np.isnan(_value(df, "inst_fund_count"))
    assert np.isnan(_value(df, "inst_net_change"))
    assert np.isnan(_value(df, "inst_multi_fund_open_flag"))


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["institutional"] is compute_institutional


def test_long_format_dataframe(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["AAPL"])
    _insert_holding(migrated_conn, cik="A")

    df = compute_institutional(migrated_conn, ASOF, ["AAPL"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value"]
    assert str(df["raw_value"].dtype) == "float64"
