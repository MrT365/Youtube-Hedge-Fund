"""Risk test fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_alembic_cfg(db_path: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def migrated_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_path = tmp_path / "risk.db"
    alembic_command.upgrade(_make_alembic_cfg(db_path), "head")
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn
    finally:
        conn.close()
