"""Insider factor tests for SCORE-07."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ls_equity_fund.factors.composer import FACTOR_REGISTRY
from ls_equity_fund.factors.insider import SUB_FACTORS, compute_insider

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


def _insert_insider(
    conn: sqlite3.Connection,
    *,
    ticker: str = "AAPL",
    line_no: int,
    transaction_code: str,
    total_value: float,
    days_before: int = 0,
    insider_name: str | None = None,
    insider_title: str | None = None,
    is_officer: int = 0,
) -> None:
    transaction_date = (ASOF - timedelta(days=days_before)).isoformat()
    conn.execute(
        """
        INSERT INTO insider_transactions (
            accession_number, line_no, ticker, insider_name, insider_title,
            is_officer, transaction_code, total_value, transaction_date, filed_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{ticker}-{line_no}",
            line_no,
            ticker,
            insider_name or f"Insider {line_no}",
            insider_title,
            is_officer,
            transaction_code,
            total_value,
            transaction_date,
            transaction_date,
        ),
    )


def _value(df: pd.DataFrame, sub_factor: str, ticker: str = "AAPL") -> float:
    return float(df.loc[(df["ticker"] == ticker) & (df["sub_factor"] == sub_factor), "raw_value"].iloc[0])


def test_three_subfactors_emitted(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(migrated_conn, line_no=1, transaction_code="P", total_value=100.0)

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert df["sub_factor"].tolist() == list(SUB_FACTORS)
    assert len(df) == 3


def test_net_flow_p_s_only_CP3_binding(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    for line_no, code, value in [
        (1, "P", 100.0),
        (2, "S", 50.0),
        (3, "A", 1000.0),
        (4, "M", 2000.0),
        (5, "F", 500.0),
    ]:
        _insert_insider(migrated_conn, line_no=line_no, transaction_code=code, total_value=value)

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_net_flow_90d") == pytest.approx(50.0)


@pytest.mark.parametrize("transaction_code", ["A", "M", "F", "G", "D"])
def test_amfgd_codes_zero_contribution(
    migrated_conn: sqlite3.Connection,
    transaction_code: str,
) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(
        migrated_conn,
        line_no=1,
        transaction_code=transaction_code,
        total_value=9000.0,
    )

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert not df["raw_value"].fillna(0.0).isin([9000.0, -9000.0]).any()
    assert int(df["sufficient_history"].max()) == 0


def test_cluster_buy_p_only(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    for idx in range(1, 5):
        _insert_insider(
            migrated_conn,
            line_no=idx,
            transaction_code="P",
            total_value=100.0,
            insider_name=f"Buyer {idx}",
        )
    for idx in range(5, 10):
        _insert_insider(
            migrated_conn,
            line_no=idx,
            transaction_code="S",
            total_value=100.0,
            insider_name=f"Seller {idx}",
        )

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_cluster_buy_count") == pytest.approx(4.0)


def test_cluster_buy_30day_window(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(
        migrated_conn,
        line_no=1,
        transaction_code="P",
        total_value=100.0,
        days_before=31,
    )

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_cluster_buy_count") == pytest.approx(0.0)


def test_ceo_cfo_3x_weight(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(
        migrated_conn,
        line_no=1,
        transaction_code="P",
        total_value=1000.0,
        insider_name="Tim Cook",
        insider_title="Chief Executive Officer",
        is_officer=1,
    )

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_ceo_cfo_buys") == pytest.approx(3000.0)


def test_ceo_cfo_excludes_S_code(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(
        migrated_conn,
        line_no=1,
        transaction_code="S",
        total_value=1000.0,
        insider_name="Tim Cook",
        insider_title="Chief Executive Officer",
        is_officer=1,
    )

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_ceo_cfo_buys") == pytest.approx(0.0)


def test_ceo_cfo_excludes_non_CEO_CFO_officer(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(
        migrated_conn,
        line_no=1,
        transaction_code="P",
        total_value=1000.0,
        insider_name="Marketing Officer",
        insider_title="Chief Marketing Officer",
        is_officer=1,
    )

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_ceo_cfo_buys") == pytest.approx(0.0)


def test_sector_median_fallback(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(
        migrated_conn,
        {
            "A": "Information Technology",
            "B": "Information Technology",
            "C": "Information Technology",
            "D": "Information Technology",
            "E": "Information Technology",
        },
    )
    for idx, (ticker, value) in enumerate(
        [("A", 100.0), ("B", 200.0), ("C", 300.0), ("D", 400.0)],
        start=1,
    ):
        _insert_insider(
            migrated_conn,
            ticker=ticker,
            line_no=idx,
            transaction_code="P",
            total_value=value,
            days_before=31,
        )

    df = compute_insider(migrated_conn, ASOF, ["A", "B", "C", "D", "E"])

    assert _value(df, "ins_net_flow_90d", "E") == pytest.approx(250.0)
    assert df.loc[df["ticker"] == "E", "sufficient_history"].eq(0).all()


def test_window_uses_calendar_days_90(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(migrated_conn, line_no=1, transaction_code="P", total_value=100.0, days_before=90)
    _insert_insider(migrated_conn, line_no=2, transaction_code="P", total_value=1000.0, days_before=91)

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert _value(df, "ins_net_flow_90d") == pytest.approx(100.0)


def test_registered_in_factor_registry() -> None:
    assert FACTOR_REGISTRY["insider"] is compute_insider


def test_long_format_DataFrame(migrated_conn: sqlite3.Connection) -> None:
    _insert_universe(migrated_conn, {"AAPL": "Information Technology"})
    _insert_insider(migrated_conn, line_no=1, transaction_code="P", total_value=100.0)

    df = compute_insider(migrated_conn, ASOF, ["AAPL"])

    assert list(df.columns) == ["ticker", "sub_factor", "raw_value", "sufficient_history"]
    assert str(df["raw_value"].dtype) == "float64"
    assert len(df) == 3


def test_grep_audit_no_unfiltered_insider_query() -> None:
    source_path = Path("src/ls_equity_fund/factors/insider.py")
    lines = source_path.read_text(encoding="utf-8").splitlines()
    offenders = [
        line
        for line in lines
        if "FROM insider_transactions" in line
        and "transaction_code =" not in line
        and "transaction_code IN" not in line
        and not line.lstrip().startswith(("#", "--"))
    ]
    assert offenders == []
