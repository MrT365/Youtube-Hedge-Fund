"""Tests for the combined composite factor."""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.factors.combined_score import compute_combined

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

BASE_FACTORS = (
    "momentum",
    "value",
    "quality",
    "growth",
    "revisions",
    "short_interest",
    "insider",
    "institutional",
)


def _migrated_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "t.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _seed_universe(conn: sqlite3.Connection, mapping: dict[str, list[str]]) -> None:
    asof = date(2026, 5, 5).isoformat()
    now = int(time.time())
    with conn:
        for sector, tickers in mapping.items():
            for ticker in tickers:
                conn.execute(
                    "INSERT INTO universe (ticker, sector, first_seen_date, inclusion_window, last_updated) "
                    "VALUES (?, ?, ?, 'active', ?)",
                    (ticker, sector, asof, now),
                )


def _seed_parents(
    conn: sqlite3.Connection,
    asof: date,
    rows: list[tuple[str, str, str, float]],
) -> None:
    """rows = (ticker, factor, sector, parent_score)."""
    now = int(time.time())
    with conn:
        for ticker, factor, sector, score in rows:
            conn.execute(
                "INSERT INTO factor_scores_parent (ticker, score_date, factor, parent_score, "
                "sector, n_subfactors_used, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, asof.isoformat(), factor, score, sector, 6, now),
            )


def test_combined_returns_expected_shape(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL", "MSFT"]})
    asof = date(2026, 5, 5)
    rows = [
        (t, f, "IT", 50.0 + i)
        for i, t in enumerate(["AAPL", "MSFT"])
        for f in BASE_FACTORS
    ]
    _seed_parents(conn, asof, rows)

    out = compute_combined(conn, asof, None)

    assert list(out.columns) == ["ticker", "sub_factor", "raw_value"]
    assert (out["sub_factor"] == "combined").all()
    assert len(out) == 2  # one row per ticker
    assert out["raw_value"].dtype == "float64"


def test_combined_equal_weights_8_factors(tmp_path: Path) -> None:
    """raw_value should be the simple mean of the 8 parent scores per ticker."""
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL"]})
    asof = date(2026, 5, 5)
    # AAPL: parent scores 10, 20, 30, 40, 50, 60, 70, 80 → mean = 45
    expected_mean = sum([10, 20, 30, 40, 50, 60, 70, 80]) / 8
    rows = [
        ("AAPL", f, "IT", float(score))
        for f, score in zip(BASE_FACTORS, [10, 20, 30, 40, 50, 60, 70, 80], strict=True)
    ]
    _seed_parents(conn, asof, rows)

    out = compute_combined(conn, asof, None)
    assert len(out) == 1
    assert out["raw_value"].iloc[0] == pytest.approx(expected_mean)


def test_combined_partial_factor_coverage(tmp_path: Path) -> None:
    """Mean uses only available factors; missing ones don't drag the mean to zero."""
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL"]})
    asof = date(2026, 5, 5)
    # Only 4 of 8 factors persisted; mean = 50.0
    rows = [
        ("AAPL", f, "IT", 50.0)
        for f in BASE_FACTORS[:4]
    ]
    _seed_parents(conn, asof, rows)

    out = compute_combined(conn, asof, None)
    assert len(out) == 1
    assert out["raw_value"].iloc[0] == pytest.approx(50.0)


def test_combined_skips_null_parent_scores(tmp_path: Path) -> None:
    """NULL parent_score (e.g. degenerate sector with N=0) is dropped from the mean."""
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL"]})
    asof = date(2026, 5, 5)
    # Insert mix: 3 valid (each 60) + 1 NULL → mean of valid = 60
    now = int(time.time())
    with conn:
        for f, val in zip(BASE_FACTORS[:3], [60.0, 60.0, 60.0], strict=True):
            conn.execute(
                "INSERT INTO factor_scores_parent (ticker, score_date, factor, parent_score, "
                "sector, n_subfactors_used, computed_at) VALUES ('AAPL', ?, ?, ?, 'IT', 6, ?)",
                (asof.isoformat(), f, val, now),
            )
        conn.execute(
            "INSERT INTO factor_scores_parent (ticker, score_date, factor, parent_score, "
            "sector, n_subfactors_used, computed_at) VALUES ('AAPL', ?, ?, NULL, 'IT', 0, ?)",
            (asof.isoformat(), BASE_FACTORS[3], now),
        )

    out = compute_combined(conn, asof, None)
    assert len(out) == 1
    assert out["raw_value"].iloc[0] == pytest.approx(60.0)


def test_combined_returns_empty_when_no_parents(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL"]})
    asof = date(2026, 5, 5)
    # No parent scores seeded
    out = compute_combined(conn, asof, None)
    assert isinstance(out, pd.DataFrame)
    assert out.empty
    assert list(out.columns) == ["ticker", "sub_factor", "raw_value"]


def test_combined_filters_to_requested_tickers(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL", "MSFT", "NVDA"]})
    asof = date(2026, 5, 5)
    rows = [(t, f, "IT", 50.0) for t in ["AAPL", "MSFT", "NVDA"] for f in BASE_FACTORS]
    _seed_parents(conn, asof, rows)

    out = compute_combined(conn, asof, ["AAPL"])
    assert set(out["ticker"]) == {"AAPL"}


def test_combined_does_not_read_combined_rows(tmp_path: Path) -> None:
    """If a 'combined' row already exists for asof, a re-run should not feed it back in."""
    conn = _migrated_db(tmp_path)
    _seed_universe(conn, {"IT": ["AAPL"]})
    asof = date(2026, 5, 5)
    rows = [("AAPL", f, "IT", 50.0) for f in BASE_FACTORS]
    _seed_parents(conn, asof, rows)
    # Pre-existing combined row from prior run with raw value of 99 — would skew the mean if read
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT INTO factor_scores_parent (ticker, score_date, factor, parent_score, "
            "sector, n_subfactors_used, computed_at) VALUES ('AAPL', ?, 'combined', 99.0, 'IT', 1, ?)",
            (asof.isoformat(), now),
        )

    out = compute_combined(conn, asof, None)
    # If the function read its own prior combined row, the mean would be (50*8 + 99)/9 ≈ 55.4.
    # The correct behavior reads only the 8 BASE_FACTORS, so mean = 50.0.
    assert out["raw_value"].iloc[0] == pytest.approx(50.0)
