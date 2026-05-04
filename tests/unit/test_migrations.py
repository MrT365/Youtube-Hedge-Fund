"""Tests for Alembic migration 0001_create_runs_table.

Verifies:
  1. After upgrade head, runs + heartbeat + alembic_version tables exist.
  2. status CHECK constraint enforced on runs.
  3. heartbeat has the singleton row pre-inserted.
  4. Re-running upgrade head is idempotent (no error, no schema change).
  5. Migration source uses op.execute (D-01) and NOT op.create_table.

Tests run alembic programmatically via alembic.config.Config + alembic.command.upgrade
against a tmp_path-derived DB so they're independent of the operator's config.yaml
and of plan 00-02's load_config implementation timing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.db import get_connection


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "migrations"
INITIAL_MIGRATION = MIGRATIONS_DIR / "versions" / "0001_create_runs_table.py"


def _make_alembic_config(db_path: Path) -> AlembicConfig:
    """Build an Alembic config pointing at a tmp DB, with absolute script_location.

    Bypasses env.py's load_config branch by setting sqlalchemy.url directly via the
    main option override — env.py's _resolve_db_url() uses the alembic.ini stub URL
    when load_config raises, but we override here to a tmp path so each test is
    hermetic.
    """
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_initial_migration_uses_raw_sql() -> None:
    """Test 5: migration file uses op.execute (D-01) and NOT op.create_table."""
    text = INITIAL_MIGRATION.read_text()
    assert "op.execute(" in text, (
        "Initial migration must use op.execute (raw SQL per D-01)"
    )
    assert "op.create_table(" not in text, (
        "op.create_table is forbidden per D-01 — use op.execute"
    )
    # Count op.execute calls — runs CREATE, runs INDEX, heartbeat CREATE,
    # heartbeat INSERT, plus 3 in downgrade. Acceptance criteria: at least 4.
    assert text.count("op.execute(") >= 4, (
        f"expected >=4 op.execute calls, found {text.count('op.execute(')}"
    )
    # Defensive: SQLAlchemy ORM type imports are forbidden in a raw-SQL migration.
    assert "MetaData" not in text, "SQLAlchemy MetaData must not appear in migration"


def test_alembic_upgrade_head_creates_tables(tmp_path: Path) -> None:
    """Test 1: After upgrade head, runs and heartbeat tables exist with the locked schema."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_config(db_path)

    command.upgrade(cfg, "head")
    assert db_path.exists(), "DB file not created by alembic upgrade"

    conn = get_connection(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        assert "runs" in tables
        assert "heartbeat" in tables
        assert "alembic_version" in tables

        # Verify runs schema matches D-02 exactly.
        cols = {
            row["name"]: (row["type"], bool(row["notnull"]), bool(row["pk"]))
            for row in conn.execute("PRAGMA table_info(runs)")
        }
        assert cols == {
            "run_id": ("TEXT", False, True),  # PK column has notnull=0 in SQLite output
            "start_ts": ("INTEGER", True, False),
            "end_ts": ("INTEGER", False, False),
            "status": ("TEXT", True, False),
            "error": ("TEXT", False, False),
        }, f"runs columns drifted from D-02: {cols}"

        # Verify the runs index exists.
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='runs'"
            )
        }
        assert "idx_runs_start_ts" in indexes
    finally:
        conn.close()


def test_runs_status_check_constraint(tmp_path: Path) -> None:
    """Test 2: status CHECK constraint rejects invalid values."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_config(db_path)
    command.upgrade(cfg, "head")

    conn = get_connection(db_path)
    try:
        # Valid status — should succeed.
        conn.execute(
            "INSERT INTO runs (run_id, start_ts, status) VALUES (?, ?, ?)",
            ("r1", 1, "OK"),
        )

        # Invalid status — should raise IntegrityError on the CHECK constraint.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_id, start_ts, status) VALUES (?, ?, ?)",
                ("r2", 2, "INVALID"),
            )

        # Other valid values also accepted.
        for status in ("RUNNING", "FAILED"):
            conn.execute(
                "INSERT INTO runs (run_id, start_ts, status) VALUES (?, ?, ?)",
                (f"r-{status}", 3, status),
            )
    finally:
        conn.close()


def test_heartbeat_singleton_row(tmp_path: Path) -> None:
    """Test 3: heartbeat table has exactly one pre-inserted row with id=1; CHECK enforces singleton."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_config(db_path)
    command.upgrade(cfg, "head")

    conn = get_connection(db_path)
    try:
        rows = list(
            conn.execute(
                "SELECT id, last_run_id, last_heartbeat_ts, last_status FROM heartbeat"
            )
        )
        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["last_run_id"] is None
        assert rows[0]["last_heartbeat_ts"] is None
        assert rows[0]["last_status"] is None

        # Singleton CHECK: id != 1 must be rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO heartbeat (id) VALUES (2)")
    finally:
        conn.close()


def test_upgrade_idempotent(tmp_path: Path) -> None:
    """Test 4: re-running upgrade head is idempotent."""
    db_path = tmp_path / "test.db"
    cfg = _make_alembic_config(db_path)

    command.upgrade(cfg, "head")
    # Second run must not error and must not change schema.
    command.upgrade(cfg, "head")

    conn = get_connection(db_path)
    try:
        # Schema unchanged — runs + heartbeat + alembic_version still the only tables.
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        assert tables == {"runs", "heartbeat", "alembic_version"}

        # alembic_version row count is exactly 1 (current head).
        version_count = conn.execute(
            "SELECT COUNT(*) FROM alembic_version"
        ).fetchone()[0]
        assert version_count == 1

        # Heartbeat singleton still exactly 1 row (idempotent re-run did not duplicate).
        heartbeat_count = conn.execute("SELECT COUNT(*) FROM heartbeat").fetchone()[0]
        assert heartbeat_count == 1
    finally:
        conn.close()
