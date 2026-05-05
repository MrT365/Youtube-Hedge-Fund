"""Short-interest factor tests for SCORE-06."""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.sector_rank import compute_sector_percentile_rank
from ls_equity_fund.factors.short_interest import SUB_FACTORS, compute_short_interest

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


def _insert_short_interest(
    conn: sqlite3.Connection,
    ticker: str,
    snapshot_date: date,
    *,
    short_percent_of_float: float | None,
    short_ratio: float | None = 1.0,
) -> None:
    conn.execute(
        """
        INSERT INTO short_interest (
            ticker, snapshot_date, shares_short, short_ratio, short_percent_of_float
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (ticker, snapshot_date.isoformat(), None, short_ratio, short_percent_of_float),
    )


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "T1") -> float:
    return float(
        df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0]
    )


def test_three_subfactors_emitted(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_short_interest(migrated_conn, "T1", ASOF, short_percent_of_float=0.10)

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value"]
    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 3
    assert str(df["raw_value"].dtype) == "float64"


def test_high_si_low_long_score(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(
        migrated_conn, {"LOW": "Information Technology", "HIGH": "Information Technology"}
    )
    _insert_short_interest(migrated_conn, "LOW", ASOF, short_percent_of_float=0.05)
    _insert_short_interest(migrated_conn, "HIGH", ASOF, short_percent_of_float=0.30)

    df = compute_short_interest(migrated_conn, ASOF, ["LOW", "HIGH"])
    pct_float = df[df["sub_factor"] == "short_pct_float_inv"].assign(
        score_date=ASOF.isoformat(),
        factor="short_interest",
        sector="Information Technology",
    )
    ranked = compute_sector_percentile_rank(pct_float)
    scores = dict(zip(ranked["ticker"], ranked["percentile_rank"], strict=True))

    assert _value(df, "short_pct_float_inv", "HIGH") == pytest.approx(-0.30)
    assert scores["HIGH"] < scores["LOW"]


def test_dtc_sign_flip(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_short_interest(
        migrated_conn,
        "T1",
        ASOF,
        short_percent_of_float=0.10,
        short_ratio=4.0,
    )

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert _value(df, "short_dtc_inv") == pytest.approx(-4.0)


def test_change_inv_declining_si_is_bullish(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_short_interest(
        migrated_conn,
        "T1",
        date(2026, 4, 4),
        short_percent_of_float=0.20,
    )
    _insert_short_interest(migrated_conn, "T1", ASOF, short_percent_of_float=0.10)

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert _value(df, "short_change_inv") == pytest.approx(0.10)


def test_change_inv_rising_si_is_bearish(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_short_interest(
        migrated_conn,
        "T1",
        date(2026, 4, 4),
        short_percent_of_float=0.10,
    )
    _insert_short_interest(migrated_conn, "T1", ASOF, short_percent_of_float=0.20)

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert _value(df, "short_change_inv") == pytest.approx(-0.10)


def test_nan_when_no_data(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert len(df) == 3
    assert df["raw_value"].isna().all()


def test_change_inv_nan_when_30d_prior_missing(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_short_interest(migrated_conn, "T1", ASOF, short_percent_of_float=0.20, short_ratio=2.0)

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert _value(df, "short_pct_float_inv") == pytest.approx(-0.20)
    assert _value(df, "short_dtc_inv") == pytest.approx(-2.0)
    assert np.isnan(_value(df, "short_change_inv"))


def test_uses_latest_snapshot_at_or_before_asof(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"T1": "Information Technology"})
    _insert_short_interest(
        migrated_conn,
        "T1",
        date(2026, 5, 3),
        short_percent_of_float=0.05,
    )
    _insert_short_interest(migrated_conn, "T1", ASOF, short_percent_of_float=0.10)
    _insert_short_interest(
        migrated_conn,
        "T1",
        date(2026, 5, 5),
        short_percent_of_float=0.20,
    )

    df = compute_short_interest(migrated_conn, ASOF, ["T1"])

    assert _value(df, "short_pct_float_inv") == pytest.approx(-0.10)


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["short_interest"] is compute_short_interest


def test_naming_uses_inv_suffix() -> None:
    assert all(name.endswith("_inv") for name in SUB_FACTORS)
