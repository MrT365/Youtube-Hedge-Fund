"""Estimate-revisions factor tests for SCORE-05."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.revisions import SUB_FACTORS, compute_revisions

ASOF = date(2026, 5, 4)


def _insert_universe(conn: sqlite3.Connection, tickers: list[str]) -> None:
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
        for ticker in tickers
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


def _insert_estimate(
    conn: sqlite3.Connection,
    ticker: str,
    snapshot_date: str,
    eps_fy1: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO analyst_estimates (ticker, snapshot_date, eps_fy1)
        VALUES (?, ?, ?)
        """,
        (ticker, snapshot_date, eps_fy1),
    )


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def _history(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> int:
    return int(
        df.loc[
            (df["ticker"] == ticker) & (df["sub_factor"] == sub_factor),
            "sufficient_history",
        ].iloc[0]
    )


def test_three_subfactors_emitted(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])
    _insert_estimate(migrated_conn, "T1", "2026-02-03", 4.50)
    _insert_estimate(migrated_conn, "T1", "2026-03-05", 4.80)
    _insert_estimate(migrated_conn, "T1", "2026-04-04", 5.00)
    _insert_estimate(migrated_conn, "T1", "2026-05-04", 5.30)

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 3


def test_revision_after_history_accrues(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])
    _insert_estimate(migrated_conn, "T1", "2026-04-04", 5.00)
    _insert_estimate(migrated_conn, "T1", "2026-05-04", 5.30)

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert _value(df, "rev_30d") == pytest.approx(0.30)
    assert _history(df, "rev_30d") == 1


def test_60d_lookback(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])
    _insert_estimate(migrated_conn, "T1", "2026-03-05", 4.80)
    _insert_estimate(migrated_conn, "T1", "2026-05-04", 5.30)

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert _value(df, "rev_60d") == pytest.approx(0.50)
    assert _history(df, "rev_60d") == 1


def test_90d_lookback(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])
    _insert_estimate(migrated_conn, "T1", "2026-02-03", 4.50)
    _insert_estimate(migrated_conn, "T1", "2026-05-04", 5.30)

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert _value(df, "rev_90d") == pytest.approx(0.80)
    assert _history(df, "rev_90d") == 1


def test_degenerate_neutral_zero(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])
    _insert_estimate(migrated_conn, "T1", "2026-05-04", 5.30)

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert _value(df, "rev_30d") == 0.0
    assert _history(df, "rev_30d") == 0


def test_degenerate_no_current_snapshot(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert df["raw_value"].tolist() == [0.0, 0.0, 0.0]
    assert df["sufficient_history"].tolist() == [0, 0, 0]


def test_uses_closest_snapshot_at_or_before_lookback(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])
    _insert_estimate(migrated_conn, "T1", "2026-04-04", 5.00)
    _insert_estimate(migrated_conn, "T1", "2026-04-15", 5.10)
    _insert_estimate(migrated_conn, "T1", "2026-05-04", 5.30)

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert _value(df, "rev_30d") == pytest.approx(0.30)


def test_returns_dataframe_with_sufficient_history_col(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, ["T1"])

    df = compute_revisions(migrated_conn, ASOF, ["T1"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value", "sufficient_history"]
    assert str(df["raw_value"].dtype) == "float64"
    assert str(df["sufficient_history"].dtype) == "int64"


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["revisions"] is compute_revisions


def test_calendar_arithmetic_ok_here() -> None:
    source = Path("src/ls_equity_fund/factors/revisions.py").read_text(encoding="utf-8")
    assert "timedelta(days=lookback_days)" in source
