"""Phase 2 migration tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_alembic_cfg(db_path: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "phase2.db"
    alembic_command.upgrade(_make_alembic_cfg(db_path), "head")
    return db_path


def test_factor_score_tables_created(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"factor_scores", "factor_scores_parent"} <= tables


def test_factor_scores_primary_key_order(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        pk_cols = [(r[1], r[5]) for r in conn.execute("PRAGMA table_info(factor_scores)") if r[5]]
    finally:
        conn.close()
    assert pk_cols == [("ticker", 1), ("score_date", 2), ("factor", 3), ("sub_factor", 4)]


def test_factor_scores_parent_primary_key_order(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        pk_cols = [
            (r[1], r[5]) for r in conn.execute("PRAGMA table_info(factor_scores_parent)") if r[5]
        ]
    finally:
        conn.close()
    assert pk_cols == [("ticker", 1), ("score_date", 2), ("factor", 3)]


def test_factor_scores_indexes_created(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(factor_scores)")}
    finally:
        conn.close()
    assert {
        "idx_fs_score_date",
        "idx_fs_ticker_date",
        "idx_fs_factor_date",
        "idx_fs_sector_date",
    } <= indexes


def test_factor_scores_insert_or_replace_idempotent(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        insert = """
            INSERT OR REPLACE INTO factor_scores (
                ticker, score_date, factor, sub_factor, raw_value,
                percentile_rank, sector, n_in_sector, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        conn.execute(insert, ("A", "2026-05-04", "momentum", "mom_6m", 1.0, 50, "Tech", 5, 1))
        conn.execute(insert, ("A", "2026-05-04", "momentum", "mom_6m", 2.0, 50, "Tech", 5, 2))
        rows = conn.execute("SELECT raw_value FROM factor_scores").fetchall()
    finally:
        conn.close()
    assert rows == [(2.0,)]


def test_factor_scores_sector_not_null(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO factor_scores (
                    ticker, score_date, factor, sub_factor, raw_value,
                    percentile_rank, n_in_sector, computed_at
                ) VALUES ('A', '2026-05-04', 'momentum', 'mom_6m', 1, 50, 5, 1)
                """
            )
    finally:
        conn.close()


def test_factor_scores_sufficient_history_defaults_to_one(migrated_db: Path) -> None:
    conn = sqlite3.connect(str(migrated_db))
    try:
        conn.execute(
            """
            INSERT INTO factor_scores (
                ticker, score_date, factor, sub_factor, raw_value,
                percentile_rank, sector, n_in_sector, computed_at
            ) VALUES ('A', '2026-05-04', 'momentum', 'mom_6m', 1, 50, 'Tech', 5, 1)
            """
        )
        value = conn.execute("SELECT sufficient_history FROM factor_scores").fetchone()[0]
    finally:
        conn.close()
    assert value == 1


def test_migration_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.db"
    cfg = _make_alembic_cfg(db_path)
    alembic_command.upgrade(cfg, "head")
    alembic_command.downgrade(cfg, "base")
    alembic_command.upgrade(cfg, "head")
