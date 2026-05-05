"""Shared fixtures for factor tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
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
    db_path = tmp_path / "factor_test.db"
    alembic_command.upgrade(_make_alembic_cfg(db_path), "head")
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def asof_date() -> pd.Timestamp:
    return pd.Timestamp("2026-05-04")


@pytest.fixture
def trading_dates(asof_date: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.bdate_range(end=asof_date, periods=300)
