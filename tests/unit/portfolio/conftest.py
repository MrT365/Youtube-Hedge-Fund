"""Phase 5 portfolio test fixtures.

``migrated_db`` returns an open sqlite3.Connection backed by an Alembic-
migrated tmp DB. ``conn`` is the same connection but already opened.
"""

from __future__ import annotations

import sqlite3
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
def migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "phase5.db"
    alembic_command.upgrade(_make_alembic_cfg(db_path), "head")
    return db_path


@pytest.fixture
def conn(migrated_db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(migrated_db))
    yield c
    c.close()
