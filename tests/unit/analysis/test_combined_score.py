"""Tests for ANAL-09 combined-score (60% quant + 40% Claude) with fallback."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.analysis import combined_score

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _migrated_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "t.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return sqlite3.connect(db_path)


def _seed_quant(conn: sqlite3.Connection, asof: date, rows: list[tuple[str, str, float]]) -> None:
    """rows = (ticker, sector, quant_score)."""
    now = int(time.time())
    with conn:
        for ticker, sector, score in rows:
            conn.execute(
                "INSERT INTO factor_scores_parent (ticker, score_date, factor, "
                "parent_score, sector, n_subfactors_used, computed_at) "
                "VALUES (?, ?, 'combined', ?, ?, 6, ?)",
                (ticker, asof.isoformat(), score, sector, now),
            )


def _put_claude_result(
    conn: sqlite3.Connection,
    *,
    analyzer: str,
    ticker: str,
    response: dict,
) -> None:
    now = int(time.time())
    with conn:
        conn.execute(
            "INSERT INTO analysis_results (analyzer_type, ticker, artifact_id, "
            "model, response_json, input_tokens, output_tokens, computed_at, "
            "expires_at) VALUES (?, ?, ?, 'm', ?, 0, 0, ?, ?)",
            (analyzer, ticker, f"art-{ticker}-{analyzer}", json.dumps(response), now,
             now + 86_400 * 30),
        )


def test_no_quant_data_returns_empty(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    df = combined_score.compute_and_persist(conn, asof=date(2026, 5, 5))
    assert df.empty


def test_quant_only_passes_through(tmp_path: Path) -> None:
    """Spec fallback: tickers with no Claude data get combined = quant_score."""
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_quant(conn, asof, [("AAPL", "IT", 80.0), ("MSFT", "IT", 60.0)])
    df = combined_score.compute_and_persist(conn, asof=asof)
    assert len(df) == 2
    assert (df["has_claude"] == False).all()  # noqa: E712
    # combined_raw == quant_score (no blend, no penalty)
    aapl = df[df["ticker"] == "AAPL"].iloc[0]
    assert aapl["combined_raw"] == pytest.approx(80.0)


def test_blend_60_40_when_claude_present(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_quant(conn, asof, [("AAPL", "IT", 80.0)])
    # Insider STRONG_BUY (90) at confidence 1.0 → score 90
    _put_claude_result(
        conn,
        analyzer="insider",
        ticker="AAPL",
        response={"signal": "STRONG_BUY", "confidence": 1.0},
    )
    df = combined_score.compute_and_persist(conn, asof=asof)
    aapl = df[df["ticker"] == "AAPL"].iloc[0]
    # quant=80 × 0.6 + claude=90 × 0.4 = 48 + 36 = 84
    assert aapl["combined_raw"] == pytest.approx(84.0)
    assert aapl["has_claude"]


def test_filing_score_averages_four_subscores(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_quant(conn, asof, [("AAPL", "IT", 50.0)])
    _put_claude_result(
        conn,
        analyzer="filing",
        ticker="AAPL",
        response={
            "earnings_quality_score": 80,
            "revenue_quality_score": 70,
            "balance_sheet_score": 60,
            "accruals_score": 50,
        },
    )
    df = combined_score.compute_and_persist(conn, asof=asof)
    aapl = df[df["ticker"] == "AAPL"].iloc[0]
    # claude_score = mean(80, 70, 60, 50) = 65
    # quant=50 × 0.6 + 65 × 0.4 = 30 + 26 = 56
    assert aapl["combined_raw"] == pytest.approx(56.0)


def test_risk_severity_to_score_mapping(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_quant(conn, asof, [("AAPL", "IT", 50.0)])
    _put_claude_result(
        conn, analyzer="risk", ticker="AAPL",
        response={"risk_severity": "low"},
    )
    df = combined_score.compute_and_persist(conn, asof=asof)
    aapl = df[df["ticker"] == "AAPL"].iloc[0]
    # risk severity low → 80; quant=50 × 0.6 + 80 × 0.4 = 30 + 32 = 62
    assert aapl["combined_raw"] == pytest.approx(62.0)


def test_persists_to_factor_scores_parent(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    _seed_quant(conn, asof, [("AAPL", "IT", 80.0), ("MSFT", "IT", 60.0)])
    combined_score.compute_and_persist(conn, asof=asof)

    rows = conn.execute(
        "SELECT ticker, parent_score FROM factor_scores_parent "
        "WHERE score_date = ? AND factor = 'combined' ORDER BY ticker",
        (asof.isoformat(),),
    ).fetchall()
    assert len(rows) == 2
    # Re-ranked within sector — best becomes 100, worst becomes 50 (N=2 → 50/100)
    by_ticker = dict(rows)
    assert by_ticker["AAPL"] == pytest.approx(100.0)
    assert by_ticker["MSFT"] == pytest.approx(50.0)


def test_re_rank_within_sector(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    # Two sectors; cross-sector order shouldn't matter for percentile rank
    _seed_quant(
        conn,
        asof,
        [
            ("AAPL", "IT", 80.0),
            ("MSFT", "IT", 60.0),
            ("JPM", "Fin", 70.0),
            ("BAC", "Fin", 30.0),
        ],
    )
    df = combined_score.compute_and_persist(conn, asof=asof)
    # Within IT: AAPL > MSFT
    it = df[df["sector"] == "IT"].sort_values("combined_score", ascending=False)
    assert it.iloc[0]["ticker"] == "AAPL"
    assert it.iloc[0]["combined_score"] == pytest.approx(100.0)
    # Within Fin: JPM > BAC
    fin = df[df["sector"] == "Fin"].sort_values("combined_score", ascending=False)
    assert fin.iloc[0]["ticker"] == "JPM"
    assert fin.iloc[0]["combined_score"] == pytest.approx(100.0)
