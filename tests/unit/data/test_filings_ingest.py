"""Filings + insider + 13F ingest tests.

Covers:
  - refresh_filings persists metadata + parses Form 4 → insider_transactions
  - cluster-buy detection counts ONLY P-codes (CP3 binding)
  - CEO/CFO P-code purchase filter
  - 13F period_end vs filed_date distinct (D4 binding — 45-day lag)
  - Multi-fund openings detection
  - Anti-hardcoded fund-name guard (CLAUDE.md)
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.data.filings import refresh_filings
from ls_equity_fund.data.insider import (
    detect_cluster_buys,
    flag_ceo_cfo_purchases,
)
from ls_equity_fund.data.institutional import (
    detect_multi_fund_openings,
    refresh_institutional_holdings,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def populated_db(tmp_path: Path):
    db = tmp_path / "test.db"
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    alembic_command.upgrade(cfg, "head")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO universe (ticker, first_seen_date, inclusion_window, last_updated) "
        "VALUES ('AAPL', '2026-01-01', '2026-01-01:current', 0)"
    )
    conn.commit()
    yield db, conn
    conn.close()


@pytest.fixture
def fake_config(tmp_path: Path):
    """Minimal Config-shaped object with .data.cache_dir + tracked_funds."""

    class FakeTrackedFund:
        def __init__(self, name: str, cik: str) -> None:
            self.name = name
            self.cik = cik

    class FakeData:
        def __init__(self) -> None:
            self.cache_dir = str(tmp_path)
            self.tracked_funds = [
                FakeTrackedFund("Berkshire Hathaway", "0001067983"),
                FakeTrackedFund("Citadel Advisors", "0001423053"),
            ]

    class FakeConfig:
        def __init__(self) -> None:
            self.data = FakeData()

    return FakeConfig()


@pytest.fixture
def fake_secrets():
    class FakeSecrets:
        sec_user_agent = "Test Operator test@example.com"

    return FakeSecrets()


# ---------- refresh_filings ----------


def test_refresh_filings_persists_metadata_and_parses_form4(
    populated_db, fake_config, fake_secrets
) -> None:
    db, conn = populated_db
    fixture = REPO_ROOT / "tests" / "fixtures" / "form4_p_purchase.xml"

    fake_provider = MagicMock()
    fake_provider.fetch_filings.return_value = [
        {
            "accession_number": "0000320193-26-000001",
            "ticker": "AAPL",
            "cik": "0000320193",
            "form_type": "4",
            "filed_date": "2026-04-15",
            "period_of_report": "2026-04-15",
            "filepath": str(fixture),
            "content_hash": "abc",
        }
    ]
    fake_provider.parse_form4.return_value = [
        {
            "accession_number": "0000320193-26-000001",
            "line_no": 1,
            "ticker": "AAPL",
            "insider_name": "Cook Timothy D",
            "insider_title": "Chief Executive Officer",
            "is_officer": 1,
            "is_director": 0,
            "is_ten_percent_owner": 0,
            "transaction_code": "P",
            "transaction_type": "ACQUIRED",
            "shares": 1000.0,
            "price_per_share": 185.5,
            "total_value": 185500.0,
            "transaction_date": "2026-04-15",
            "filed_date": "",
            "ownership_type": "D",
        }
    ]

    result = refresh_filings(
        fake_config,
        fake_secrets,
        conn=conn,
        forms=["4"],
        tickers=["AAPL"],
        today=date(2026, 4, 16),
        provider=fake_provider,
    )
    assert result["ok"] == 1
    assert result["filings_inserted"] == 1
    assert result["insider_inserted"] == 1

    fm = conn.execute(
        "SELECT form_type, transaction_code FROM filings_metadata fm "
        "JOIN insider_transactions it USING(accession_number) "
        "WHERE fm.ticker='AAPL'"
    ).fetchone()
    assert fm["form_type"] == "4"
    assert fm["transaction_code"] == "P"


def test_refresh_filings_idempotent_on_repeat(
    populated_db, fake_config, fake_secrets
) -> None:
    """Idempotency: re-running with same accession does NOT duplicate rows."""
    db, conn = populated_db
    fixture = REPO_ROOT / "tests" / "fixtures" / "form4_p_purchase.xml"

    fake_provider = MagicMock()
    fake_provider.fetch_filings.return_value = [
        {
            "accession_number": "0000320193-26-000001",
            "ticker": "AAPL",
            "cik": "0000320193",
            "form_type": "4",
            "filed_date": "2026-04-15",
            "period_of_report": "2026-04-15",
            "filepath": str(fixture),
            "content_hash": "abc",
        }
    ]
    fake_provider.parse_form4.return_value = [
        {
            "accession_number": "0000320193-26-000001",
            "line_no": 1,
            "ticker": "AAPL",
            "insider_name": "Cook",
            "insider_title": "CEO",
            "is_officer": 1,
            "is_director": 0,
            "is_ten_percent_owner": 0,
            "transaction_code": "P",
            "transaction_type": "ACQUIRED",
            "shares": 1000.0,
            "price_per_share": 185.5,
            "total_value": 185500.0,
            "transaction_date": "2026-04-15",
            "filed_date": "",
            "ownership_type": "D",
        }
    ]

    refresh_filings(
        fake_config, fake_secrets, conn=conn, forms=["4"],
        tickers=["AAPL"], today=date(2026, 4, 16), provider=fake_provider,
    )
    refresh_filings(
        fake_config, fake_secrets, conn=conn, forms=["4"],
        tickers=["AAPL"], today=date(2026, 4, 16), provider=fake_provider,
    )

    # Both filings_metadata and insider_transactions are PK-keyed; INSERT OR
    # IGNORE keeps row count at 1.
    assert conn.execute(
        "SELECT COUNT(*) FROM filings_metadata"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM insider_transactions"
    ).fetchone()[0] == 1


# ---------- cluster-buy detection ----------


def test_cluster_buys_count_only_p_codes(populated_db) -> None:
    """CP3 binding — A/M/F codes excluded from cluster signal."""
    _, conn = populated_db
    base = (
        "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
        "insider_name, transaction_code, transaction_date, filed_date, total_value) "
        "VALUES (?, 1, 'AAPL', ?, ?, '2026-04-15', '2026-04-15', ?)"
    )
    # 4 P-purchases by 4 distinct insiders
    for i, name in enumerate(["A", "B", "C", "D"]):
        conn.execute(base, (f"a{i}", name, "P", 100000.0))
    # 2 A-grants — should NOT count toward cluster
    conn.execute(base, ("a4", "E", "A", 50000.0))
    conn.execute(base, ("a5", "F", "A", 50000.0))

    clusters = detect_cluster_buys(conn, today=date(2026, 4, 30))
    assert len(clusters) == 1
    assert clusters[0]["ticker"] == "AAPL"
    assert clusters[0]["distinct_insiders"] == 4  # A,B,C,D — NOT E,F


def test_cluster_buys_below_threshold_excluded(populated_db) -> None:
    _, conn = populated_db
    conn.execute(
        "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
        "insider_name, transaction_code, transaction_date, filed_date, total_value) "
        "VALUES ('x', 1, 'AAPL', 'X', 'P', '2026-04-15', '2026-04-15', 100.0)"
    )
    clusters = detect_cluster_buys(
        conn, today=date(2026, 4, 30), min_insiders=3
    )
    assert clusters == []


def test_cluster_buys_outside_window_excluded(populated_db) -> None:
    """Buys older than window_days don't count."""
    _, conn = populated_db
    base = (
        "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
        "insider_name, transaction_code, transaction_date, filed_date, total_value) "
        "VALUES (?, 1, 'AAPL', ?, 'P', ?, ?, 100.0)"
    )
    # 3 P-purchases 90 days ago — outside default 30-day window
    for i, name in enumerate(["A", "B", "C"]):
        conn.execute(base, (f"old{i}", name, "2026-01-01", "2026-01-01"))
    clusters = detect_cluster_buys(conn, today=date(2026, 4, 30))
    assert clusters == []


# ---------- CEO/CFO filter ----------


def test_ceo_cfo_purchases_filter(populated_db) -> None:
    _, conn = populated_db
    base = (
        "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
        "insider_name, insider_title, is_officer, transaction_code, "
        "transaction_date, filed_date, total_value) "
        "VALUES (?, 1, 'AAPL', ?, ?, 1, 'P', '2026-04-01', '2026-04-01', ?)"
    )
    conn.execute(base, ("a1", "Cook", "Chief Executive Officer", 100000.0))
    conn.execute(base, ("a2", "Maestri", "Chief Financial Officer", 50000.0))
    conn.execute(base, ("a3", "Smith", "VP Engineering", 25000.0))  # NOT CEO/CFO

    out = flag_ceo_cfo_purchases(conn, today=date(2026, 4, 30))
    titles = {r["insider_title"] for r in out}
    assert titles == {"Chief Executive Officer", "Chief Financial Officer"}
    assert "VP Engineering" not in titles


# ---------- 13F D4 binding ----------


def test_13f_45_day_lag_preserved_via_separate_columns(populated_db) -> None:
    """D4 binding — period_end and filed_date are distinct."""
    _, conn = populated_db
    conn.execute(
        """INSERT INTO institutional_holdings
           (cik, fund_name, ticker, period_end, filed_date,
            shares, value_usd, change_shares, is_new_position)
           VALUES ('0001067983', 'Berkshire', 'AAPL',
                   '2026-03-31', '2026-05-15',
                   1000000, 185000000, 100000, 0)"""
    )
    row = conn.execute(
        "SELECT period_end, filed_date FROM institutional_holdings "
        "WHERE ticker='AAPL'"
    ).fetchone()
    assert row["period_end"] == "2026-03-31"
    assert row["filed_date"] == "2026-05-15"
    assert row["period_end"] != row["filed_date"]  # D4 — never collapse


def test_refresh_13f_persists_period_end_and_filed_date(
    populated_db, fake_config, fake_secrets
) -> None:
    """End-to-end: refresh_institutional_holdings preserves D4 distinction."""
    _, conn = populated_db

    fake_provider = MagicMock()

    def fetch_filings_side_effect(cik, forms, since=None, cache_dir=None):
        # Two funds in fake_config — return one filing each
        return [
            {
                "accession_number": f"{cik}-13F-Q1",
                "ticker": cik,
                "cik": cik,
                "form_type": "13F-HR",
                "filed_date": "2026-05-15",
                "period_of_report": "2026-03-31",
                "filepath": "/tmp/fake.xml",
                "content_hash": "h",
            }
        ]

    def parse_13f_side_effect(accession, path):
        return [
            {
                "ticker": "AAPL",
                "shares": 1000.0,
                "value_usd": 185000.0,
                "cusip": "037833100",
            }
        ]

    fake_provider.fetch_filings.side_effect = fetch_filings_side_effect
    fake_provider.parse_13f.side_effect = parse_13f_side_effect

    result = refresh_institutional_holdings(
        fake_config, fake_secrets, conn=conn, provider=fake_provider
    )
    assert result["ok"] == 2  # both funds OK
    assert result["rows_written"] == 2

    rows = conn.execute(
        "SELECT period_end, filed_date FROM institutional_holdings "
        "WHERE ticker='AAPL' ORDER BY cik"
    ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["period_end"] == "2026-03-31"
        assert row["filed_date"] == "2026-05-15"
        assert row["period_end"] != row["filed_date"]


def test_multi_fund_openings(populated_db) -> None:
    _, conn = populated_db
    base = (
        "INSERT INTO institutional_holdings (cik, fund_name, ticker, "
        "period_end, filed_date, shares, is_new_position) "
        "VALUES (?, ?, 'XYZ', '2026-03-31', '2026-05-15', ?, 1)"
    )
    conn.execute(base, ("c1", "Fund1", 100))
    conn.execute(base, ("c2", "Fund2", 200))
    conn.execute(base, ("c3", "Fund3", 300))

    out = detect_multi_fund_openings(
        conn, period_end="2026-03-31", min_funds=3
    )
    assert len(out) == 1
    assert out[0]["ticker"] == "XYZ"
    assert out[0]["new_funds"] == 3


# ---------- Anti-hardcoded guard ----------


def test_no_hardcoded_fund_names_in_institutional_module() -> None:
    """CLAUDE.md anti-recommendation — fund names live in config, not source."""
    src = (
        REPO_ROOT / "src" / "ls_equity_fund" / "data" / "institutional.py"
    ).read_text()
    forbidden = ["Citadel", "Berkshire", "Pershing", "Bridgewater", "Tiger Global"]
    for name in forbidden:
        assert name not in src, (
            f"Hardcoded fund name '{name}' in institutional.py — "
            f"must come from config.data.tracked_funds"
        )


def test_no_hardcoded_fund_names_in_filings_module() -> None:
    src = (
        REPO_ROOT / "src" / "ls_equity_fund" / "data" / "filings.py"
    ).read_text()
    forbidden = ["Citadel", "Berkshire", "Pershing", "Bridgewater", "Tiger Global"]
    for name in forbidden:
        assert name not in src, (
            f"Hardcoded fund name '{name}' in filings.py"
        )
