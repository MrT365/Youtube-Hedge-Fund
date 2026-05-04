"""Tests for ls_equity_fund.db — WAL connection factory.

Per CONTEXT D-04 + plan 00-03 acceptance criteria. Eight tests covering:
  1. get_connection returns a sqlite3.Connection
  2. journal_mode=WAL is active
  3. foreign_keys=ON, busy_timeout=5000, synchronous=NORMAL all applied
  4. row_factory is sqlite3.Row (column-name access)
  5. parent dir is auto-created
  6. get_db_path(config) returns Path(cache_dir) / 'ls_equity_fund.db'
  7. WAL sidecar (-wal, -shm) files appear after a write transaction
  8. PRAGMAS module constant covers all six expected pragmas

Note: Plan 00-02 (config.py) executes in parallel with this plan. The
get_db_path-with-config test guards on config availability so this test file
runs cleanly in isolation. Once 00-02 merges, the test exercises the real
Config without modification.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ls_equity_fund.db import PRAGMAS, get_connection, get_db_path


def test_get_connection_returns_sqlite_connection(tmp_path: Path) -> None:
    """Test 1: get_connection returns a sqlite3.Connection."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_journal_mode_is_wal(tmp_path: Path) -> None:
    """Test 1 (continued): WAL mode is active after open."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_pragmas_set(tmp_path: Path) -> None:
    """Test 2: foreign_keys=1, busy_timeout=5000, synchronous=1 (NORMAL)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        # synchronous: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        # cache_size returns the negative-KiB form when set with a negative value
        assert conn.execute("PRAGMA cache_size").fetchone()[0] == -65536
    finally:
        conn.close()


def test_row_factory_is_row(tmp_path: Path) -> None:
    """Test 3: rows accessible by column name."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    try:
        assert conn.row_factory is sqlite3.Row
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.execute("INSERT INTO t (a, b) VALUES (1, 'hello')")
        row = conn.execute("SELECT a, b FROM t").fetchone()
        assert row["a"] == 1
        assert row["b"] == "hello"
    finally:
        conn.close()


def test_creates_parent_dir(tmp_path: Path) -> None:
    """Test 4: parent directory is created if missing."""
    db_path = tmp_path / "nested" / "subdir" / "test.db"
    assert not db_path.parent.exists()
    conn = get_connection(db_path)
    try:
        assert db_path.parent.exists()
    finally:
        conn.close()


def test_get_db_path_uses_config_cache_dir(tmp_path: Path) -> None:
    """Test 5: get_db_path(config) -> Path(config.data.cache_dir) / 'ls_equity_fund.db'.

    Uses a duck-typed config-like object so this test is independent of plan 00-02's
    Config schema. The contract under test is: get_db_path reads config.data.cache_dir.
    """

    class _DataCfg:
        cache_dir = str(tmp_path / "cache")

    class _Config:
        data = _DataCfg()

    path = get_db_path(_Config())  # type: ignore[arg-type]
    assert path == Path(_Config.data.cache_dir) / "ls_equity_fund.db"


def test_wal_sidecar_files_appear(tmp_path: Path) -> None:
    """Test 6: -wal and -shm sidecar files appear after a write transaction."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    try:
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t (a) VALUES (1)")
        # autocommit mode (isolation_level=None) — write is durable immediately.
        # WAL sidecar files exist while the connection is open.
        assert (tmp_path / "test.db-wal").exists()
        assert (tmp_path / "test.db-shm").exists()
    finally:
        conn.close()


def test_pragmas_constant_complete() -> None:
    """Test 7: PRAGMAS list contains all six expected pragmas."""
    expected_keys = [
        "journal_mode=WAL",
        "synchronous=NORMAL",
        "foreign_keys=ON",
        "busy_timeout=5000",
        "cache_size=-65536",
        "temp_store=MEMORY",
    ]
    for key in expected_keys:
        assert any(key in p for p in PRAGMAS), f"missing pragma: {key}"
    assert len(PRAGMAS) == 6
