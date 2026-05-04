"""SQLite gateway — single source of connection setup for the project.

Per ARCHITECTURE.md §4 and CONTEXT D-01..D-05:
    - WAL mode, foreign keys ON, 5s busy timeout, 64MB cache.
    - Every layer that persists imports `from ls_equity_fund.db import get_connection`.
    - Migrations are sole schema source-of-truth (D-04); raw SQL only (D-01).

This module is intentionally tiny: connection setup + path resolution. Schema lives
exclusively in `migrations/versions/`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — type-only import
    # Plan 00-02 ships ls_equity_fund.config.Config; this is a forward reference so
    # this module imports cleanly even when config.py is absent (e.g., during the
    # parallel-execution window of Phase 0). Runtime callers pass the Config in.
    from ls_equity_fund.config import Config


# PRAGMAs applied on every connection — kept as a module constant so tests can
# assert the contract directly (test_pragmas_constant_complete).
PRAGMAS: list[str] = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",       # safe + fast under WAL
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",        # 5s wait before raising "database is locked"
    "PRAGMA cache_size=-65536",        # 64MB page cache (negative => KiB)
    "PRAGMA temp_store=MEMORY",
]


def get_db_path(config: "Config | None" = None) -> Path:
    """Resolve the SQLite path from config.data.cache_dir.

    If `config` is None, falls back to reading `ls_equity_fund.config.load_config()`.
    Used by Alembic env.py and by callers that already have a Config in hand.

    Example: cache_dir="cache" -> Path("cache/ls_equity_fund.db")
    """
    if config is None:
        # Lazy import — keeps this module importable even when config.py is not yet
        # ship-ed (Phase 0 parallel-execution window). Real callers always pass a
        # Config explicitly; this branch is a convenience for env.py.
        from ls_equity_fund.config import load_config  # noqa: PLC0415

        config, _ = load_config()
    cache_dir = Path(config.data.cache_dir)
    return cache_dir / "ls_equity_fund.db"


def get_connection(
    db_path: str | Path,
    *,
    create_parent: bool = True,
) -> sqlite3.Connection:
    """Open a connection to the SQLite DB and apply project PRAGMAs.

    Args:
        db_path: filesystem path to the .db file.
        create_parent: if True, mkdir -p the parent directory before opening.

    Returns:
        sqlite3.Connection with row_factory=Row, type-detection enabled,
        all six PRAGMAs applied.

    Notes:
        - isolation_level=None puts sqlite3 in autocommit mode; callers wrap
          multi-statement work in explicit BEGIN/COMMIT (or use `with conn:`).
        - PARSE_DECLTYPES + PARSE_COLNAMES enable timestamp adapters.
    """
    db_path = Path(db_path)
    if create_parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row

    for pragma in PRAGMAS:
        conn.execute(pragma)

    return conn


__all__ = ["PRAGMAS", "get_db_path", "get_connection"]
