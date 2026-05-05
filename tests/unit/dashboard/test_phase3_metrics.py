"""Phase-3 SC2/SC3/SC4 tests for the 10 metric cards + Page II + auto-refresh."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.dashboard import app as dashboard_app
from ls_equity_fund.dashboard import queries

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _migrated_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "t.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return sqlite3.connect(db_path)


def _seed_universe(conn: sqlite3.Connection, by_sector: dict[str, list[str]]) -> None:
    asof = date(2026, 5, 5).isoformat()
    now = int(time.time())
    with conn:
        for sector, tickers in by_sector.items():
            for t in tickers:
                conn.execute(
                    "INSERT INTO universe (ticker, sector, first_seen_date, "
                    "inclusion_window, last_updated) VALUES (?, ?, ?, 'active', ?)",
                    (t, sector, asof, now),
                )


def _seed_combined(
    conn: sqlite3.Connection,
    asof: date,
    rows: list[tuple[str, str, float]],
) -> None:
    """rows = (ticker, sector, combined_score). Inserts factor='combined' rows."""
    now = int(time.time())
    with conn:
        for ticker, sector, score in rows:
            conn.execute(
                "INSERT INTO factor_scores_parent (ticker, score_date, factor, "
                "parent_score, sector, n_subfactors_used, computed_at) "
                "VALUES (?, ?, 'combined', ?, ?, 6, ?)",
                (ticker, asof.isoformat(), score, sector, now),
            )


# --- 10-metric-card queries (SC2) ------------------------------------------


def test_long_short_candidate_counts(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_universe(conn, {"IT": ["A", "B", "C", "D", "E"]})
    _seed_combined(
        conn,
        asof,
        [
            ("A", "IT", 95.0),  # long
            ("B", "IT", 85.0),  # long
            ("C", "IT", 50.0),  # neither
            ("D", "IT", 18.0),  # short
            ("E", "IT", 5.0),  # short
        ],
    )
    assert queries.long_candidate_count(conn, asof) == 2
    assert queries.short_candidate_count(conn, asof) == 2


def test_position_count_returns_zero_when_table_missing(tmp_path: Path) -> None:
    """Phase 5 ships portfolio_positions; Phase 3 must not crash without it."""
    conn = _migrated_db(tmp_path)
    assert queries.position_count(conn) == 0


def test_crowding_count_three_funds(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    pe = "2026-03-31"
    fd = "2026-05-15"
    with conn:
        # AAPL: 4 funds new — qualifies
        for i, cik in enumerate(("0001", "0002", "0003", "0004")):
            conn.execute(
                "INSERT INTO institutional_holdings (cik, fund_name, ticker, "
                "period_end, filed_date, shares, value_usd, change_shares, "
                "is_new_position) VALUES (?, ?, 'AAPL', ?, ?, 100, 1000, 100, 1)",
                (cik, f"Fund-{i}", pe, fd),
            )
        # MSFT: 2 funds new — does NOT qualify (< 3)
        for i, cik in enumerate(("0005", "0006")):
            conn.execute(
                "INSERT INTO institutional_holdings (cik, fund_name, ticker, "
                "period_end, filed_date, shares, value_usd, change_shares, "
                "is_new_position) VALUES (?, ?, 'MSFT', ?, ?, 100, 1000, 100, 1)",
                (cik, f"Fund-{i}", pe, fd),
            )
    assert queries.crowding_count(conn) == 1


def test_insider_event_and_ceo_buy_counts(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    recent = (asof - timedelta(days=10)).isoformat()
    old = (asof - timedelta(days=120)).isoformat()
    with conn:
        # 3 P-purchases in window: 1 CEO, 1 director, 1 generic
        conn.execute(
            "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
            "transaction_date, filed_date, insider_name, insider_title, "
            "transaction_code, shares, price_per_share, total_value) "
            "VALUES ('a-1', 1, 'AAPL', ?, ?, 'Tim Cook', 'CEO', 'P', 1000, 1.0, 1000)",
            (recent, recent),
        )
        conn.execute(
            "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
            "transaction_date, filed_date, insider_name, insider_title, "
            "transaction_code, shares, price_per_share, total_value) "
            "VALUES ('a-2', 1, 'AAPL', ?, ?, 'Director X', 'Director', 'P', 100, 1.0, 100)",
            (recent, recent),
        )
        conn.execute(
            "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
            "transaction_date, filed_date, insider_name, insider_title, "
            "transaction_code, shares, price_per_share, total_value) "
            "VALUES ('a-3', 1, 'AAPL', ?, ?, 'Other', 'VP', 'P', 50, 1.0, 50)",
            (recent, recent),
        )
        # An A-grant (NOT directional) — must NOT count toward insider events
        conn.execute(
            "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
            "transaction_date, filed_date, insider_name, insider_title, "
            "transaction_code, shares, price_per_share, total_value) "
            "VALUES ('a-4', 1, 'AAPL', ?, ?, 'Tim Cook', 'CEO', 'A', 5000, 1.0, 5000)",
            (recent, recent),
        )
        # Old P-purchase (outside window)
        conn.execute(
            "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
            "transaction_date, filed_date, insider_name, insider_title, "
            "transaction_code, shares, price_per_share, total_value) "
            "VALUES ('a-5', 1, 'AAPL', ?, ?, 'Stale', 'Director', 'P', 100, 1.0, 100)",
            (old, old),
        )
    assert queries.insider_event_count(conn, asof) == 3
    assert queries.ceo_buy_count(conn, asof) == 1


def test_cluster_buy_count(tmp_path: Path) -> None:
    """3+ distinct insiders P-purchasing same ticker within 30 days."""
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    recent = (asof - timedelta(days=5)).isoformat()
    with conn:
        # AAPL: 3 distinct insiders → cluster buy (qualifies)
        for i, name in enumerate(("Alice", "Bob", "Carol")):
            conn.execute(
                "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
                "transaction_date, filed_date, insider_name, insider_title, "
                "transaction_code, shares, price_per_share, total_value) "
                "VALUES (?, 1, 'AAPL', ?, ?, ?, 'Director', 'P', 100, 1.0, 100)",
                (f"a-{i}", recent, recent, name),
            )
        # MSFT: 2 distinct insiders → does NOT qualify
        for i, name in enumerate(("X", "Y")):
            conn.execute(
                "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
                "transaction_date, filed_date, insider_name, insider_title, "
                "transaction_code, shares, price_per_share, total_value) "
                "VALUES (?, 1, 'MSFT', ?, ?, ?, 'Director', 'P', 100, 1.0, 100)",
                (f"m-{i}", recent, recent, name),
            )
    assert queries.cluster_buy_count(conn, asof) == 1


def test_vix_close_returns_latest(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    with conn:
        for d, close in (("2026-05-04", 18.2), ("2026-05-05", 14.8)):
            conn.execute(
                "INSERT INTO daily_prices (ticker, date, open, high, low, close, "
                "volume, adj_close) VALUES ('^VIX', ?, ?, ?, ?, ?, 0, ?)",
                (d, close, close, close, close, close),
            )
    assert queries.vix_close(conn, date(2026, 5, 5)) == 14.8


def test_vix_close_returns_none_without_data(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    assert queries.vix_close(conn, date(2026, 5, 5)) is None


def test_vix_regime_buckets() -> None:
    assert queries.vix_regime(None) == ("UNKNOWN", "muted")
    assert queries.vix_regime(12.0) == ("CALM", "long")
    assert queries.vix_regime(20.0) == ("NORMAL", "muted")
    assert queries.vix_regime(28.0) == ("ELEVATED", "warn")
    assert queries.vix_regime(50.0) == ("CRISIS", "short")


def test_earnings_in_n_days(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    now = int(time.time())
    with conn:
        for ticker, dt in (
            ("AAPL", asof.isoformat()),  # day 0
            ("MSFT", (asof + timedelta(days=3)).isoformat()),  # day 3
            ("NVDA", (asof + timedelta(days=10)).isoformat()),  # day 10 (out)
        ):
            conn.execute(
                "INSERT INTO earnings_calendar (ticker, expected_date, refreshed_at) "
                "VALUES (?, ?, ?)",
                (ticker, dt, now),
            )
    assert queries.earnings_in_n_days(conn, asof, days=7) == 2


def test_data_provider_label_yfinance(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    with conn:
        conn.execute(
            "INSERT INTO daily_prices (ticker, date, open, high, low, close, "
            "volume, adj_close) VALUES ('AAPL', '2026-05-05', 1, 1, 1, 1, 1, 1)"
        )
    assert "yfinance" in queries.data_provider_label(conn)


def test_data_provider_label_no_data(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    assert queries.data_provider_label(conn) == "no data yet"


# --- Page II (SC3) ----------------------------------------------------------


def test_factor_heatmap_combines_top_and_bottom(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    rows = [(f"T{i:03d}", "IT", float(i * 2)) for i in range(70)]
    _seed_combined(conn, asof, rows)
    df = queries.factor_heatmap(conn, asof, top=30, bottom=30)
    assert len(df) == 60
    # First 30 are top by combined; next 30 are bottom
    top_tickers = list(df["ticker"].iloc[:30])
    bottom_tickers = list(df["ticker"].iloc[30:])
    assert top_tickers[0] == "T069"  # highest combined
    assert bottom_tickers[0] == "T000"  # lowest combined


def test_candidate_cards_long_side_pulls_quality_subfactors(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_combined(conn, asof, [("AAPL", "IT", 95.0), ("MSFT", "IT", 60.0)])
    now = int(time.time())
    # AAPL has Piotroski + Altman raw values
    with conn:
        conn.execute(
            "INSERT INTO factor_scores (ticker, score_date, factor, sub_factor, "
            "raw_value, percentile_rank, sector, n_in_sector, sufficient_history, "
            "computed_at) VALUES ('AAPL', ?, 'quality', 'qual_piotroski_f', 8, 90, "
            "'IT', 2, 1, ?)",
            (asof.isoformat(), now),
        )
        conn.execute(
            "INSERT INTO factor_scores (ticker, score_date, factor, sub_factor, "
            "raw_value, percentile_rank, sector, n_in_sector, sufficient_history, "
            "computed_at) VALUES ('AAPL', ?, 'quality', 'qual_altman_z', 4.5, 90, "
            "'IT', 2, 1, ?)",
            (asof.isoformat(), now),
        )
    df = queries.candidate_cards(conn, asof, side="long", n=2)
    aapl = df[df["ticker"] == "AAPL"].iloc[0]
    assert aapl["piotroski_f"] == 8
    assert aapl["altman_z"] == pytest.approx(4.5)
    assert aapl["altman_zone"] == "safe"


def test_candidate_cards_altman_zones() -> None:
    assert queries._altman_zone_label(3.5) == "safe"
    assert queries._altman_zone_label(2.0) == "grey"
    assert queries._altman_zone_label(1.0) == "distress"
    assert queries._altman_zone_label(None) == ""


def test_candidate_cards_short_side_orders_ascending(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_combined(
        conn,
        asof,
        [("LOW", "IT", 5.0), ("MID", "IT", 50.0), ("HIGH", "IT", 95.0)],
    )
    df = queries.candidate_cards(conn, asof, side="short", n=2)
    assert list(df["ticker"]) == ["LOW", "MID"]


def test_candidate_cards_unknown_side_raises(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    with pytest.raises(ValueError):
        queries.candidate_cards(conn, date(2026, 5, 5), side="bogus")


def test_crowding_warnings(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    pe = "2026-03-31"
    fd = "2026-05-15"
    with conn:
        for i, cik in enumerate(("0001", "0002", "0003", "0004", "0005")):
            conn.execute(
                "INSERT INTO institutional_holdings (cik, fund_name, ticker, "
                "period_end, filed_date, shares, value_usd, change_shares, "
                "is_new_position) VALUES (?, ?, 'AAPL', ?, ?, 100, 1000, 100, 1)",
                (cik, f"Fund-{i}", pe, fd),
            )
    df = queries.crowding_warnings(conn)
    assert len(df) == 1
    assert df.iloc[0]["ticker"] == "AAPL"
    assert df.iloc[0]["n_new_funds"] == 5


# --- SC4 — auto-refresh predicate -------------------------------------------


def test_market_open_weekday_inside_hours() -> None:
    et = ZoneInfo("America/New_York")
    # Tuesday 2026-05-05 at 11:00 ET
    now = datetime(2026, 5, 5, 11, 0, tzinfo=et)
    assert dashboard_app._is_market_open(now) is True


def test_market_closed_before_open() -> None:
    et = ZoneInfo("America/New_York")
    # Tuesday 2026-05-05 at 09:29 ET (one minute before open)
    now = datetime(2026, 5, 5, 9, 29, tzinfo=et)
    assert dashboard_app._is_market_open(now) is False


def test_market_closed_at_close_time() -> None:
    et = ZoneInfo("America/New_York")
    # 16:00 sharp is closed (window is < not <=)
    now = datetime(2026, 5, 5, 16, 0, tzinfo=et)
    assert dashboard_app._is_market_open(now) is False


def test_market_closed_weekend() -> None:
    et = ZoneInfo("America/New_York")
    # Saturday 2026-05-09 at noon
    now = datetime(2026, 5, 9, 12, 0, tzinfo=et)
    assert dashboard_app._is_market_open(now) is False
