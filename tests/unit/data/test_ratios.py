"""24 derived ratios computation tests (DATA-04).

Math is exercised against an integer-valued synthetic fixture so floating-point
slop does not mask wrong formulas. The 24-name + count test pins the spec
contract directly.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.data.ratios import _OUTPUT_COLS, compute_all_ratios, compute_ratios

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def populated_db(tmp_path: Path):
    db = tmp_path / "test.db"
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    alembic_command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Universe row
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('AAPL', '2026-01-01', '2026-01-01:current', 0)"
    )
    # Five quarterly snapshots — q0 = most recent (2026-03-31), q4 = year-ago
    # (2025-03-31). Columns ordered to match the INSERT below.
    # Tuple: (period_end, revenue, net_income, cfo, total_assets, total_equity,
    #         current_assets, current_liabilities, accounts_receivable,
    #         gross_profit, long_term_debt, retained_earnings, total_liabilities,
    #         shares_outstanding, free_cash_flow, ebit, accruals, dividends_paid,
    #         buybacks, working_capital, rd_expense, operating_income)
    quarters = [
        # q0 — latest, integer-friendly numbers for math verification
        (
            "2026-03-31",
            100.0,
            20.0,
            25.0,
            1000.0,
            500.0,
            200.0,
            50.0,
            30.0,
            40.0,
            100.0,
            250.0,
            600.0,
            100.0,
            15.0,
            30.0,
            -5.0,
            -3.0,
            -2.0,
            150.0,
            8.0,
            25.0,
        ),
        (
            "2025-12-31",
            95.0,
            18.0,
            22.0,
            980.0,
            490.0,
            195.0,
            50.0,
            28.0,
            38.0,
            100.0,
            245.0,
            590.0,
            100.0,
            14.0,
            28.0,
            -4.0,
            -2.5,
            -1.5,
            145.0,
            7.5,
            23.0,
        ),
        (
            "2025-09-30",
            92.0,
            17.0,
            21.0,
            970.0,
            480.0,
            190.0,
            50.0,
            26.0,
            37.0,
            100.0,
            240.0,
            585.0,
            100.0,
            14.0,
            26.0,
            -4.0,
            -2.0,
            -1.0,
            140.0,
            7.0,
            22.0,
        ),
        (
            "2025-06-30",
            90.0,
            16.0,
            20.0,
            960.0,
            470.0,
            185.0,
            50.0,
            24.0,
            36.0,
            100.0,
            235.0,
            580.0,
            100.0,
            14.0,
            24.0,
            -4.0,
            -1.5,
            -1.0,
            135.0,
            7.0,
            21.0,
        ),
        # q4 — year-ago for YoY math: rev=88, ni=14
        (
            "2025-03-31",
            88.0,
            14.0,
            18.0,
            950.0,
            460.0,
            180.0,
            50.0,
            22.0,
            35.0,
            100.0,
            230.0,
            575.0,
            100.0,
            14.0,
            22.0,
            -4.0,
            -1.0,
            -0.5,
            130.0,
            6.5,
            20.0,
        ),
    ]
    for q in quarters:
        conn.execute(
            "INSERT INTO fundamentals "
            "(ticker, period_end, period_type, as_of_ingest_date, "
            " revenue, net_income, cfo, total_assets, total_equity, "
            " current_assets, current_liabilities, accounts_receivable, "
            " gross_profit, long_term_debt, retained_earnings, total_liabilities, "
            " shares_outstanding, free_cash_flow, ebit, accruals, dividends_paid, "
            " buybacks, working_capital, rd_expense, operating_income) "
            "VALUES ('AAPL', ?, 'quarterly', '2026-04-01', "
            "        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            q,
        )
    # Latest close for market_cap — close=150, shares=100 → mcap=15000
    conn.execute(
        "INSERT INTO daily_prices (ticker, date, close) VALUES ('AAPL', '2026-04-01', 150.0)"
    )
    conn.commit()
    yield conn
    conn.close()


def test_24_output_cols_match_spec() -> None:
    """REQUIREMENTS.md DATA-04 lists 24 ratios — pin the count."""
    assert len(_OUTPUT_COLS) == 24


def test_basic_ratios_correct(populated_db) -> None:
    r = compute_ratios("AAPL", date(2026, 4, 1), populated_db)
    # roe = net_income(20) / total_equity(500) = 0.04
    assert r["roe"] == pytest.approx(0.04)
    # roa = net_income(20) / total_assets(1000) = 0.02
    assert r["roa"] == pytest.approx(0.02)
    # net_margin = net_income(20) / revenue(100) = 0.2
    assert r["net_margin"] == pytest.approx(0.2)
    # gross_margin = gross_profit(40) / revenue(100) = 0.4
    assert r["gross_margin"] == pytest.approx(0.4)
    # operating_margin = op_inc(25) / revenue(100) = 0.25
    assert r["operating_margin"] == pytest.approx(0.25)


def test_yoy_growth_uses_q0_vs_q4(populated_db) -> None:
    r = compute_ratios("AAPL", date(2026, 4, 1), populated_db)
    # rev: q0=100, q4=88 → (100-88)/88 ≈ 0.1364
    assert r["revenue_growth_yoy"] == pytest.approx((100 - 88) / 88, rel=1e-4)
    # ni: q0=20, q4=14 → (20-14)/14 ≈ 0.4286
    assert r["earnings_growth_yoy"] == pytest.approx((20 - 14) / 14, rel=1e-4)


def test_qoq_growth_uses_q0_vs_q1(populated_db) -> None:
    r = compute_ratios("AAPL", date(2026, 4, 1), populated_db)
    # rev: q0=100, q1=95 → (100-95)/95
    assert r["revenue_growth_qoq"] == pytest.approx((100 - 95) / 95, rel=1e-4)
    # ni: q0=20, q1=18 → (20-18)/18
    assert r["earnings_growth_qoq"] == pytest.approx((20 - 18) / 18, rel=1e-4)


def test_yields_use_market_cap(populated_db) -> None:
    r = compute_ratios("AAPL", date(2026, 4, 1), populated_db)
    # market_cap = shares(100) * close(150) = 15000
    # fcf_yield = fcf(15) / 15000 = 0.001
    assert r["fcf_yield"] == pytest.approx(15.0 / 15000)
    # buyback_yield = -(-2) / 15000 = 2/15000
    assert r["buyback_yield"] == pytest.approx(2.0 / 15000)
    # dividend_yield = -(-3) / 15000 = 3/15000
    assert r["dividend_yield"] == pytest.approx(3.0 / 15000)


def test_balance_sheet_ratios_correct(populated_db) -> None:
    r = compute_ratios("AAPL", date(2026, 4, 1), populated_db)
    # current_ratio = ca(200) / cl(50) = 4.0
    assert r["current_ratio"] == pytest.approx(4.0)
    # ar_to_revenue = ar(30) / rev(100) = 0.3
    assert r["ar_to_revenue"] == pytest.approx(0.3)
    # debt_to_equity = lt_debt(100) / equity(500) = 0.2
    assert r["debt_to_equity"] == pytest.approx(0.2)
    # cfo_to_ni = cfo(25) / ni(20) = 1.25
    assert r["cfo_to_ni"] == pytest.approx(1.25)
    # accruals_ratio = accruals(-5) / total_assets(1000) = -0.005
    assert r["accruals_ratio"] == pytest.approx(-0.005)
    # asset_turnover = rev(100) / assets(1000) = 0.1
    assert r["asset_turnover"] == pytest.approx(0.1)


def test_normalized_ratios_correct(populated_db) -> None:
    r = compute_ratios("AAPL", date(2026, 4, 1), populated_db)
    # retained_earnings_ratio = retained(250) / total_assets(1000) = 0.25
    assert r["retained_earnings_ratio"] == pytest.approx(0.25)
    # working_capital_ratio = wc(150) / total_assets(1000) = 0.15
    assert r["working_capital_ratio"] == pytest.approx(0.15)
    # total_liabilities_ratio = tl(600) / total_assets(1000) = 0.6
    assert r["total_liabilities_ratio"] == pytest.approx(0.6)
    # ebit_margin = ebit(30) / revenue(100) = 0.3
    assert r["ebit_margin"] == pytest.approx(0.3)
    # rd_intensity = rd(8) / revenue(100) = 0.08
    assert r["rd_intensity"] == pytest.approx(0.08)
    # shares_out = 100 (passthrough)
    assert r["shares_out"] == pytest.approx(100.0)


def test_safe_div_returns_none_on_zero_revenue(populated_db) -> None:
    """Edge: revenue = 0 → ratios with revenue in denominator are None,
    not Inf or NaN. The _safe_div guard is what enforces this."""
    populated_db.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('ZERO', '2026-01-01', '2026-01-01:current', 0)"
    )
    populated_db.execute(
        "INSERT INTO fundamentals "
        "(ticker, period_end, period_type, as_of_ingest_date, revenue, net_income) "
        "VALUES ('ZERO', '2026-03-31', 'quarterly', '2026-04-01', 0.0, 5.0)"
    )
    populated_db.commit()
    r = compute_ratios("ZERO", date(2026, 4, 1), populated_db)
    assert r["net_margin"] is None
    assert r["gross_margin"] is None
    assert r["asset_turnover"] is None  # revenue/total_assets — total_assets is None


def test_compute_all_ratios_writes_row_per_ticker(populated_db) -> None:
    n = compute_all_ratios(populated_db, date(2026, 4, 1))
    assert n == 1  # 1 active ticker
    row = populated_db.execute(
        "SELECT roe, roa, net_margin, asset_turnover FROM fundamental_ratios "
        "WHERE ticker='AAPL' AND asof_date='2026-04-01'"
    ).fetchone()
    assert row is not None
    assert row["roe"] == pytest.approx(0.04)
    assert row["roa"] == pytest.approx(0.02)
    assert row["net_margin"] == pytest.approx(0.2)
    assert row["asset_turnover"] == pytest.approx(0.1)


def test_compute_all_ratios_idempotent_via_replace(populated_db) -> None:
    """INSERT OR REPLACE keyed by (ticker, asof_date) — second run overwrites,
    not appends."""
    compute_all_ratios(populated_db, date(2026, 4, 1))
    compute_all_ratios(populated_db, date(2026, 4, 1))  # second run
    n = populated_db.execute(
        "SELECT COUNT(*) FROM fundamental_ratios WHERE ticker='AAPL' AND asof_date='2026-04-01'"
    ).fetchone()[0]
    assert n == 1  # not 2


def test_returns_none_when_fundamentals_absent(populated_db) -> None:
    populated_db.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('NEW', '2026-04-01', '2026-04-01:current', 0)"
    )
    populated_db.commit()
    r = compute_ratios("NEW", date(2026, 4, 1), populated_db)
    assert all(v is None for v in r.values())


def test_pit_aware_uses_latest_as_of_ingest(populated_db) -> None:
    """If the same period_end has two as_of_ingest_date rows (D2 restated),
    compute_ratios reads the LATEST as_of_ingest_date <= asof.
    """
    # Append a restated revenue row for 2026-03-31, ingested 2026-04-15
    populated_db.execute(
        "INSERT INTO fundamentals "
        "(ticker, period_end, period_type, as_of_ingest_date, "
        " revenue, net_income, total_assets, total_equity) "
        "VALUES ('AAPL', '2026-03-31', 'quarterly', '2026-04-15', "
        "        110.0, 22.0, 1000.0, 500.0)"
    )
    populated_db.commit()

    # As of 2026-04-10 — restated row not yet visible, ratios use revenue=100
    r_pre = compute_ratios("AAPL", date(2026, 4, 10), populated_db)
    assert r_pre["net_margin"] == pytest.approx(20.0 / 100.0)

    # As of 2026-05-01 — restated row visible, ratios use revenue=110, ni=22
    r_post = compute_ratios("AAPL", date(2026, 5, 1), populated_db)
    assert r_post["net_margin"] == pytest.approx(22.0 / 110.0)
