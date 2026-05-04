"""Alembic env hook for ls_equity_fund.

Per CONTEXT D-04: migrations are the source of truth (no schema.sql).
Per CONTEXT D-01: migrations use raw op.execute() — NO SQLAlchemy ORM types.
Per CONTEXT D-05: column changes use op.batch_alter_table() (SQLite ALTER limits);
                  render_as_batch=True is set globally below so that Just Works.

This env.py:
  1. Loads config.yaml via ls_equity_fund.config.load_config so the DB path comes
     from one canonical place (data.cache_dir).
  2. Sets render_as_batch=True so any future batch_alter_table calls work.
  3. Does NOT use SQLAlchemy MetaData — target_metadata is None on purpose
     (autogenerate is deliberately disabled per D-01).
"""
from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object — gives us access to alembic.ini values.
config = context.config

# Wire Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_db_url() -> str:
    """Resolve the SQLite URL for migrations.

    Strategy:
      1. If ls_equity_fund.config.load_config() succeeds, use config.data.cache_dir.
      2. Otherwise fall back to alembic.ini's stub URL (sqlalchemy.url).

    The fallback path is exercised by `alembic check` in CI without config.yaml.
    Production (`uv run alembic upgrade head` after `cp config.yaml.example config.yaml`)
    always hits branch 1.
    """
    try:
        from ls_equity_fund.config import load_config  # noqa: PLC0415
        from ls_equity_fund.db import get_db_path  # noqa: PLC0415
    except ImportError:
        # config.py is not yet on the import path (Phase 0 parallel-execution
        # window). Fall back to the stub URL in alembic.ini.
        return config.get_main_option("sqlalchemy.url") or "sqlite:///./cache/ls_equity_fund.db"

    try:
        project_config, _ = load_config(yaml_path=Path("config.yaml"))
    except (FileNotFoundError, TypeError):
        # No config.yaml present, or load_config signature differs from expected.
        # Use stub URL.
        return config.get_main_option("sqlalchemy.url") or "sqlite:///./cache/ls_equity_fund.db"

    db_path = get_db_path(project_config)
    # Ensure parent directory exists for online-mode connection.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


# Resolve once and inject into the alembic config so both offline + online modes see it.
_db_url = _resolve_db_url()
config.set_main_option("sqlalchemy.url", _db_url)

# We do NOT use SQLAlchemy MetaData / autogenerate (per D-01).
# All migrations are hand-written raw SQL via op.execute().
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without a DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # D-05 — batch_alter_table for SQLite ALTER limits
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — open a real connection.

    Alembic uses SQLAlchemy briefly here (for transaction wrapping + alembic_version
    table tracking). Application code uses sqlite3 directly via ls_equity_fund.db;
    the SQLAlchemy hop is migration-only.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Apply project PRAGMAs to the migration connection too (best-effort —
        # journal_mode is a per-database property, foreign_keys is per-connection).
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # D-05 — required for column add/drop/rename on SQLite
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
