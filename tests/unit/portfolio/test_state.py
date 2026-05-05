"""Portfolio state persistence tests (PORT-06)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from ls_equity_fund.portfolio.state import (
    PORTFOLIO_AGGREGATE_TICKER,
    close_position,
    load_current_positions,
    upsert_position,
    write_portfolio_history,
    write_position_approvals,
)


def test_upsert_position_round_trip(conn: sqlite3.Connection) -> None:
    upsert_position(
        conn,
        ticker="AAPL",
        side="long",
        shares=100,
        entry_price=180.0,
        entry_date=date(2026, 5, 1),
        current_price=190.0,
        sector="Tech",
        factor_scores_at_entry={"momentum": 88.0, "quality": 92.0},
        beta_at_entry=1.10,
    )
    df = load_current_positions(conn)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "AAPL"
    payload = json.loads(df.iloc[0]["factor_scores_at_entry"])
    assert payload["momentum"] == 88.0
    # unrealized_pnl = (190-180) * 100 = $1000
    assert abs(df.iloc[0]["unrealized_pnl"] - 1000.0) < 1e-6


def test_upsert_overwrites_existing_row(conn: sqlite3.Connection) -> None:
    upsert_position(
        conn,
        ticker="AAPL",
        side="long",
        shares=100,
        entry_price=180,
        entry_date=date(2026, 5, 1),
        current_price=190.0,
        sector="Tech",
        factor_scores_at_entry={},
        beta_at_entry=1.0,
    )
    upsert_position(
        conn,
        ticker="AAPL",
        side="long",
        shares=200,
        entry_price=180,
        entry_date=date(2026, 5, 1),
        current_price=200.0,
        sector="Tech",
        factor_scores_at_entry={},
        beta_at_entry=1.0,
    )
    df = load_current_positions(conn)
    assert len(df) == 1
    assert df.iloc[0]["shares"] == 200
    # unrealized = (200-180) * 200 = $4000
    assert abs(df.iloc[0]["unrealized_pnl"] - 4000.0) < 1e-6


def test_close_position(conn: sqlite3.Connection) -> None:
    upsert_position(
        conn,
        ticker="AAPL",
        side="long",
        shares=100,
        entry_price=180,
        entry_date=date(2026, 5, 1),
        current_price=190.0,
        sector="Tech",
        factor_scores_at_entry={},
        beta_at_entry=1.0,
    )
    close_position(conn, ticker="AAPL", side="long")
    assert load_current_positions(conn).empty


def test_write_position_approvals(conn: sqlite3.Connection) -> None:
    rows = [
        {
            "ticker": "AAPL",
            "side": "long",
            "tilt_bucket": "top5",
            "base_weight": 0.0375,
            "tilted_weight": 0.0563,
            "adv_capped_weight": 0.0563,
            "earnings_halved": False,
            "beta_adjusted_weight": 0.0500,
            "final_weight": 0.0500,
            "final_shares": 100.0,
            "target_dollar": 50_000.0,
            "limit_price": 500.0,
            "score": 95.0,
            "sector": "Tech",
            "beta": 1.10,
            "advisory_flags": ["earnings_within_2d"],
        }
    ]
    n = write_position_approvals(
        conn,
        run_id="test-run",
        asof=date(2026, 5, 1),
        rows=rows,
        optimizer="conviction",
    )
    assert n == 1
    cur = conn.execute(
        "SELECT optimizer, tilt_bucket, base_weight, final_weight, advisory_flags "
        "FROM position_approvals WHERE ticker = 'AAPL'"
    )
    row = cur.fetchone()
    assert row[0] == "conviction"
    assert row[1] == "top5"
    assert abs(row[2] - 0.0375) < 1e-9
    assert abs(row[3] - 0.0500) < 1e-9
    flags = json.loads(row[4])
    assert "earnings_within_2d" in flags


def test_write_portfolio_history_includes_aggregate(conn: sqlite3.Connection) -> None:
    per_pos = [
        {
            "ticker": "AAPL",
            "side": "long",
            "shares": 100,
            "mark_price": 200.0,
            "market_value": 20_000.0,
            "weight": 0.04,
            "unrealized_pnl": 0.0,
            "beta": 1.10,
            "sector": "Tech",
        }
    ]
    aggregate = {
        "gross_exposure": 1.50,
        "net_exposure": 0.05,
        "net_beta": 0.08,
        "long_book_beta": 1.10,
        "short_book_beta": 1.00,
    }
    n = write_portfolio_history(
        conn,
        asof=date(2026, 5, 1),
        per_position_rows=per_pos,
        aggregate=aggregate,
    )
    assert n == 2  # 1 per-position + 1 aggregate
    rows = list(
        conn.execute(
            "SELECT ticker, gross_exposure, net_beta FROM portfolio_history "
            "WHERE asof_date = '2026-05-01' ORDER BY ticker"
        )
    )
    tickers = [r[0] for r in rows]
    assert PORTFOLIO_AGGREGATE_TICKER in tickers
    aggr = next(r for r in rows if r[0] == PORTFOLIO_AGGREGATE_TICKER)
    assert abs(aggr[1] - 1.50) < 1e-9
    assert abs(aggr[2] - 0.08) < 1e-9
