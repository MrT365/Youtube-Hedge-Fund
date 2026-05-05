"""Tests for the analysis_results SQLite cache (ANAL-04)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.analysis import cache as analysis_cache

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _migrated_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "t.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return sqlite3.connect(db_path)


def test_put_then_get_roundtrip(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    payload = {"earnings_quality_score": 75, "risk_level": "low"}
    analysis_cache.put(
        conn,
        analyzer_type="filing",
        ticker="AAPL",
        artifact_id="8q-2026-05-05",
        run_id="run-1",
        model="claude-sonnet-4-5",
        response=payload,
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.012,
    )

    hit = analysis_cache.get(
        conn, analyzer_type="filing", ticker="AAPL", artifact_id="8q-2026-05-05"
    )
    assert hit is not None
    assert hit.response == payload
    assert hit.input_tokens == 1000
    assert hit.output_tokens == 200
    assert hit.run_id == "run-1"
    assert hit.model == "claude-sonnet-4-5"
    assert hit.cost_usd == pytest.approx(0.012)


def test_get_returns_none_on_miss(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    hit = analysis_cache.get(
        conn, analyzer_type="filing", ticker="MISSING", artifact_id="x"
    )
    assert hit is None


def test_expired_row_returns_none(tmp_path: Path) -> None:
    """30-day default TTL — expired rows must NOT serve."""
    conn = _migrated_db(tmp_path)
    # Insert with 1-second TTL (ttl_days=0 would be too coarse; use a custom now_ts)
    past = int(time.time()) - 86_400 * 31  # 31 days ago
    analysis_cache.put(
        conn,
        analyzer_type="risk",
        ticker="AAPL",
        artifact_id="acc-1",
        run_id=None,
        model="m",
        response={"x": 1},
        input_tokens=0,
        output_tokens=0,
        ttl_days=30,
        now_ts=past,
    )
    # Should be expired now
    assert analysis_cache.get(
        conn, analyzer_type="risk", ticker="AAPL", artifact_id="acc-1"
    ) is None


def test_put_replaces_existing_row(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    for response, cost in (({"v": 1}, 0.10), ({"v": 2}, 0.20)):
        analysis_cache.put(
            conn,
            analyzer_type="insider",
            ticker="MSFT",
            artifact_id="insider-2026-05-05-90d",
            run_id="r1",
            model="m",
            response=response,
            input_tokens=100,
            output_tokens=50,
            cost_usd=cost,
        )
    hit = analysis_cache.get(
        conn,
        analyzer_type="insider",
        ticker="MSFT",
        artifact_id="insider-2026-05-05-90d",
    )
    assert hit is not None
    assert hit.response == {"v": 2}
    assert hit.cost_usd == pytest.approx(0.20)


def test_evict_expired_deletes_old_rows(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    past = int(time.time()) - 86_400 * 35
    for ticker in ("AAA", "BBB", "CCC"):
        analysis_cache.put(
            conn,
            analyzer_type="filing",
            ticker=ticker,
            artifact_id="x",
            run_id=None,
            model="m",
            response={"v": 1},
            input_tokens=0,
            output_tokens=0,
            now_ts=past,
            ttl_days=30,
        )
    # Plus one fresh row
    analysis_cache.put(
        conn,
        analyzer_type="filing",
        ticker="FRESH",
        artifact_id="x",
        run_id=None,
        model="m",
        response={"v": 1},
        input_tokens=0,
        output_tokens=0,
    )
    n = analysis_cache.evict_expired(conn)
    assert n == 3
    assert analysis_cache.get(
        conn, analyzer_type="filing", ticker="FRESH", artifact_id="x"
    ) is not None
    assert analysis_cache.get(
        conn, analyzer_type="filing", ticker="AAA", artifact_id="x"
    ) is None


def test_unknown_analyzer_type_raises(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    with pytest.raises(ValueError):
        analysis_cache.get(
            conn, analyzer_type="bogus", ticker="X", artifact_id="x"
        )


def test_stats_aggregates_per_analyzer(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    for analyzer in ("filing", "filing", "risk"):
        analysis_cache.put(
            conn,
            analyzer_type=analyzer,
            ticker="AAPL",
            artifact_id=f"art-{analyzer}-{time.time_ns()}",
            run_id=None,
            model="m",
            response={"v": 1},
            input_tokens=0,
            output_tokens=0,
        )
    stats = analysis_cache.stats(conn)
    assert stats["filing"]["rows"] == 2
    assert stats["risk"]["rows"] == 1
