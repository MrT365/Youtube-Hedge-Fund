"""Momentum factor tests for SCORE-01."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.momentum import SUB_FACTORS, compute_momentum

ASOF = date(2026, 5, 4)


def _insert_universe(conn: sqlite3.Connection, sectors: dict[str, str]) -> None:
    rows = [
        (
            ticker,
            f"{ticker} Corp",
            "NYSE",
            "NYSE",
            sector,
            None,
            None,
            "2025-01-01",
            None,
            "active",
            1,
        )
        for ticker, sector in sectors.items()
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


def _price_series(
    trading_dates: pd.DatetimeIndex,
    *,
    default: float = 100.0,
    overrides_by_offset: dict[int, float] | None = None,
) -> list[float]:
    prices = [default] * len(trading_dates)
    for offset, value in (overrides_by_offset or {}).items():
        prices[-1 - offset] = value
    return prices


def _insert_prices(
    conn: sqlite3.Connection,
    trading_dates: pd.DatetimeIndex,
    ticker: str,
    prices: list[float],
) -> None:
    conn.executemany(
        "INSERT INTO daily_prices (ticker, date, close, adj_close) VALUES (?, ?, ?, ?)",
        [
            (ticker, dt.date().isoformat(), float(close), float(close))
            for dt, close in zip(trading_dates, prices, strict=True)
        ],
    )


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def test_six_subfactors_emitted(migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_prices(migrated_conn, trading_dates, "T1", _price_series(trading_dates))

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 6


def test_mom_12_1_skips_last_month(
    migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex
) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    prices = _price_series(trading_dates, overrides_by_offset={21: 110.0, 252: 100.0})
    _insert_prices(migrated_conn, trading_dates, "T1", prices)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_12_1") == pytest.approx(0.10)


def test_mom_6m(migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    prices = _price_series(trading_dates, overrides_by_offset={0: 120.0, 126: 100.0})
    _insert_prices(migrated_conn, trading_dates, "T1", prices)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_6m") == pytest.approx(0.20)


def test_mom_3m(migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    prices = _price_series(trading_dates, overrides_by_offset={0: 115.0, 63: 100.0})
    _insert_prices(migrated_conn, trading_dates, "T1", prices)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_3m") == pytest.approx(0.15)


def test_mom_accel_is_3m_minus_6m(
    migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex
) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    prices = _price_series(
        trading_dates,
        overrides_by_offset={0: 120.0, 63: 120.0 / 1.15, 126: 100.0},
    )
    _insert_prices(migrated_conn, trading_dates, "T1", prices)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_3m") == pytest.approx(0.15)
    assert _value(df, "mom_6m") == pytest.approx(0.20)
    assert _value(df, "mom_accel") == pytest.approx(-0.05)


def test_mom_52w_high(migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    prices = _price_series(trading_dates, overrides_by_offset={0: 180.0, 10: 200.0})
    _insert_prices(migrated_conn, trading_dates, "T1", prices)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_52w_high") == pytest.approx(0.90)


def test_mom_sector_rel(migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    stock = _price_series(trading_dates, overrides_by_offset={0: 120.0, 126: 100.0})
    etf = _price_series(trading_dates, overrides_by_offset={0: 110.0, 126: 100.0})
    _insert_prices(migrated_conn, trading_dates, "T1", stock)
    _insert_prices(migrated_conn, trading_dates, "XLK", etf)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_sector_rel") == pytest.approx(1.20 / 1.10)


def test_insufficient_history_yields_nan(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    dates = pd.bdate_range(end=pd.Timestamp(ASOF), periods=100)
    _insert_prices(migrated_conn, dates, "T1", _price_series(dates))

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert np.isnan(_value(df, "mom_12_1"))
    assert np.isnan(_value(df, "mom_6m"))
    assert np.isnan(_value(df, "mom_52w_high"))
    assert len(df) == 6


def test_no_calendar_arithmetic() -> None:
    source = Path("src/ls_equity_fund/factors/momentum.py").read_text(encoding="utf-8")
    assert "timedelta(days=" not in source


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["momentum"] is compute_momentum


def test_returns_long_format_dataframe(
    migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex
) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_prices(migrated_conn, trading_dates, "T1", _price_series(trading_dates))

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value"]
    assert str(df["raw_value"].dtype) == "float64"


def test_multiple_tickers(migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex) -> None:
    _insert_universe(
        migrated_conn,
        {
            "T1": "Information Technology",
            "T2": "Health Care",
            "T3": "Energy",
        },
    )
    for ticker in ("T1", "T2", "T3"):
        _insert_prices(migrated_conn, trading_dates, ticker, _price_series(trading_dates))

    df = compute_momentum(migrated_conn, ASOF, ["T1", "T2", "T3"])

    assert len(df) == 18
    assert df.groupby(["ticker", "sub_factor"]).size().eq(1).all()


def test_sector_etf_missing_yields_nan(
    migrated_conn: sqlite3.Connection, trading_dates: pd.DatetimeIndex
) -> None:
    _insert_universe(migrated_conn, {"T1": "Unknown Sector"})
    prices = _price_series(trading_dates, overrides_by_offset={0: 120.0, 126: 100.0})
    _insert_prices(migrated_conn, trading_dates, "T1", prices)

    df = compute_momentum(migrated_conn, ASOF, ["T1"])

    assert _value(df, "mom_6m") == pytest.approx(0.20)
    assert np.isnan(_value(df, "mom_sector_rel"))


def test_momentum_does_not_rank() -> None:
    source = Path("src/ls_equity_fund/factors/momentum.py").read_text(encoding="utf-8")
    assert "rankdata" not in source
    assert "method=\"average\"" not in source
    assert "method='average'" not in source
