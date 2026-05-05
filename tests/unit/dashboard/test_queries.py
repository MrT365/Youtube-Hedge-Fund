"""Tests for the dashboard read-only query layer."""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.dashboard import queries

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

BASE_FACTORS = (
    "momentum", "value", "quality", "growth",
    "revisions", "short_interest", "insider", "institutional",
)


def _migrated_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "t.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return sqlite3.connect(db_path)


def _seed(
    conn: sqlite3.Connection,
    asof: date,
    by_sector: dict[str, list[str]],
    score_fn: callable,  # type: ignore[type-arg]
) -> None:
    """Seed universe + factor_scores_parent for every base factor + 'combined'.

    score_fn(ticker, factor) -> float in [0, 100]
    """
    now = int(time.time())
    with conn:
        for sector, tickers in by_sector.items():
            for t in tickers:
                conn.execute(
                    "INSERT INTO universe (ticker, sector, first_seen_date, "
                    "inclusion_window, last_updated) VALUES (?, ?, ?, 'active', ?)",
                    (t, sector, asof.isoformat(), now),
                )
                for f in (*BASE_FACTORS, "combined"):
                    conn.execute(
                        "INSERT INTO factor_scores_parent (ticker, score_date, factor, "
                        "parent_score, sector, n_subfactors_used, computed_at) "
                        "VALUES (?, ?, ?, ?, ?, 6, ?)",
                        (t, asof.isoformat(), f, float(score_fn(t, f)), sector, now),
                    )


def test_latest_score_date_returns_iso(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(conn, asof, {"IT": ["AAPL"]}, lambda t, f: 50.0)
    assert queries.latest_score_date(conn) == asof


def test_latest_score_date_none_on_empty(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    assert queries.latest_score_date(conn) is None


def test_top_candidates_orders_by_combined(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(
        conn,
        asof,
        {"IT": ["AAPL", "MSFT", "NVDA"]},
        lambda t, f: {"AAPL": 30.0, "MSFT": 80.0, "NVDA": 60.0}[t],
    )
    df = queries.top_candidates(conn, asof, top=10)
    assert df.iloc[0]["ticker"] == "MSFT"
    assert df.iloc[1]["ticker"] == "NVDA"
    assert df.iloc[2]["ticker"] == "AAPL"
    assert list(df["rank"]) == [1, 2, 3]


def test_top_candidates_min_score_filter(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(
        conn,
        asof,
        {"IT": ["AAPL", "MSFT", "NVDA"]},
        lambda t, f: {"AAPL": 30.0, "MSFT": 80.0, "NVDA": 60.0}[t],
    )
    df = queries.top_candidates(conn, asof, top=10, min_score=50.0)
    assert set(df["ticker"]) == {"MSFT", "NVDA"}


def test_top_candidates_sector_filter(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(
        conn,
        asof,
        {"IT": ["AAPL"], "Fin": ["JPM"]},
        lambda t, f: 50.0,
    )
    df = queries.top_candidates(conn, asof, top=10, sectors=["IT"])
    assert set(df["ticker"]) == {"AAPL"}


def test_factor_breakdown_wide_shape(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(conn, asof, {"IT": ["AAPL", "MSFT"]}, lambda t, f: 50.0)
    df = queries.factor_breakdown(conn, asof, ["AAPL", "MSFT"])
    assert list(df.columns) == ["ticker", *BASE_FACTORS, "combined"]
    assert set(df["ticker"]) == {"AAPL", "MSFT"}
    assert (df.drop(columns="ticker") == 50.0).all().all()


def test_factor_breakdown_preserves_input_order(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(conn, asof, {"IT": ["AAPL", "MSFT", "NVDA"]}, lambda t, f: 50.0)
    df = queries.factor_breakdown(conn, asof, ["NVDA", "AAPL", "MSFT"])
    assert list(df["ticker"]) == ["NVDA", "AAPL", "MSFT"]


def test_factor_breakdown_empty_when_no_tickers(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    df = queries.factor_breakdown(conn, date(2026, 5, 5), [])
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == ["ticker", *BASE_FACTORS, "combined"]


def test_sector_distribution(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(
        conn,
        asof,
        {"IT": ["AAPL", "MSFT"], "Fin": ["JPM"]},
        lambda t, f: 50.0,
    )
    df = queries.sector_distribution(conn, asof, top=10)
    by_sector = dict(zip(df["sector"], df["count"], strict=True))
    assert by_sector == {"IT": 2, "Fin": 1}


def test_universe_size_excludes_delisted(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    now = int(time.time())
    asof = date(2026, 5, 5)
    with conn:
        conn.execute(
            "INSERT INTO universe (ticker, sector, first_seen_date, inclusion_window, "
            "last_updated) VALUES ('AAPL','IT', ?, 'active', ?)",
            (asof.isoformat(), now),
        )
        conn.execute(
            "INSERT INTO universe (ticker, sector, first_seen_date, delisted_date, "
            "inclusion_window, last_updated) VALUES ('OLD','IT', ?, ?, 'active', ?)",
            (asof.isoformat(), asof.isoformat(), now),
        )
    assert queries.universe_size(conn) == 1


def test_available_sectors_from_combined_only(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed(conn, asof, {"IT": ["AAPL"], "Fin": ["JPM"]}, lambda t, f: 50.0)
    sectors = queries.available_sectors(conn, asof)
    assert sectors == ["Fin", "IT"]
