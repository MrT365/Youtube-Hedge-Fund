"""``meridian doctor`` — Phase 0 smoke check (CONTEXT D-25, INFRA-08, Phase 0 SC2).

Steps:
  1. Locate ``config.yaml`` (cwd or via ``--config``).
  2. Locate ``.env`` (cwd or via ``--env``). Doctor does NOT initialize ``.env`` (D-25).
  3. ``load_config()`` — pydantic-settings validation fires here (D-15).
  4. ``configure_logging(config.logging)``.
  5. ``bind_run_id(uuid4())`` (D-19).
  6. Open SQLite in WAL mode at ``get_db_path(config)`` (D-22 / db.py).
  7. ``alembic upgrade head`` against that DB (D-04).
  8. Verify required tables exist post-migration (runs, heartbeat, alembic_version).
  9. Emit structured ``"doctor passed"`` log + ``doctor passed`` stdout banner; exit 0.

Re-running on a healthy system is idempotent (D-25): alembic.command.upgrade is a
no-op when already at head; the WAL pragma is persistent across opens.

Exit code map (per ``threat_model`` T-00-23 / T-00-24 / T-00-25):
  0 — success
  2 — config.yaml not found
  3 — .env not found (operator must copy .env.example; doctor does NOT init secrets)
  4 — config validation failed (pydantic.ValidationError or similar)
  5 — DB journal_mode is not WAL after open
  6 — alembic upgrade failed
  7 — required tables missing after migration
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
import typer
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.config import load_config
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.logging import bind_run_id, configure_logging

REPO_ROOT_HINT = "Run from the repo root (the directory containing config.yaml.example)."

# Required post-migration tables. ``alembic_version`` is auto-managed by alembic
# itself; ``runs`` + ``heartbeat`` are scoped to Phase 0's initial migration (D-02).
_REQUIRED_TABLES: frozenset[str] = frozenset({"runs", "heartbeat", "alembic_version"})


def doctor(
    config_path: Path = typer.Option(
        Path("config.yaml"),
        "--config",
        help="Path to config.yaml (default: ./config.yaml)",
    ),
    env_path: Path = typer.Option(
        Path(".env"),
        "--env",
        help="Path to .env (default: ./.env)",
    ),
) -> None:
    """Phase 0 smoke check — load config, open WAL DB, apply migrations, exit 0."""
    # --- Step 1: locate config.yaml ---
    if not config_path.exists():
        typer.secho(
            f"ERROR: config.yaml not found at {config_path.resolve()}.\n"
            f"Hint: copy config.yaml.example to config.yaml and edit per-machine values.\n"
            f"{REPO_ROOT_HINT}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    # --- Step 2: locate .env (D-25 — doctor verifies, does NOT initialize) ---
    if not env_path.exists():
        typer.secho(
            f"ERROR: .env not found at {env_path.resolve()}.\n"
            f"Hint: copy .env.example to .env and fill in ANTHROPIC_API_KEY + SEC_USER_AGENT.\n"
            f"Doctor verifies; it does NOT initialize secrets (D-25).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=3)

    # --- Step 3: load_config — pydantic validation may raise here ---
    try:
        config, _secrets = load_config(yaml_path=config_path, env_path=env_path)
    except typer.Exit:
        raise
    except Exception as e:  # surface any error to operator
        typer.secho(
            f"ERROR: failed to load config: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=4) from e

    # --- Step 4: configure logging (D-20 single point) ---
    configure_logging(config.logging)
    log = structlog.get_logger("doctor")

    # --- Step 5: bind run_id (D-19) ---
    run_id = str(uuid.uuid4())
    bind_run_id(run_id)
    log.info(
        "doctor_started",
        config_path=str(config_path),
        env_path=str(env_path),
    )

    # --- Step 6: open SQLite in WAL mode at the configured path ---
    db_path = get_db_path(config)
    log.info("opening_db", path=str(db_path))
    conn = get_connection(db_path)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    if journal_mode.lower() != "wal":
        log.error("doctor_journal_mode_not_wal", got=journal_mode)
        typer.secho(
            f"ERROR: DB journal_mode={journal_mode!r}, expected 'wal'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=5)

    # --- Step 7: alembic upgrade head ---
    log.info("running_migrations")
    alembic_cfg = AlembicConfig("alembic.ini")
    # Override sqlalchemy.url so alembic targets the resolved db_path even if
    # CWD has drifted from the project root in a weird invocation.
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    try:
        alembic_command.upgrade(alembic_cfg, "head")
    except Exception as e:  # surface any migration error
        log.error("migration_failed", error=str(e))
        typer.secho(
            f"ERROR: alembic upgrade head failed: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=6) from e

    # NOTE: alembic's env.py calls ``logging.config.fileConfig(alembic.ini)``
    # which (per stdlib default) sets ``disable_existing_loggers=True``. That
    # flag (a) wipes the stdlib root handlers we attached in Step 4, (b) lowers
    # root to WARNING (per alembic.ini's ``[logger_root]`` section), AND
    # (c) sets ``disabled=True`` on every previously-named logger including
    # ``"doctor"``. Without restoring our pipeline, post-alembic events
    # (notably ``doctor_passed``) silently drop. Recovery sequence:
    #   1. Re-enable every disabled named logger (fileConfig set them all dead).
    #   2. Reset structlog's logger cache (cache_logger_on_first_use=True caches
    #      bound loggers and survives a configure_logging re-run otherwise).
    #   3. Clear the configure_logging idempotency guard so it re-attaches
    #      handlers and resets the root level to ``config.logging.level``.
    #   4. Re-bind run_id and re-fetch the doctor logger so the cached chain
    #      points at the live handler set.
    import logging as _stdlib_logging

    import ls_equity_fund.logging as _log_mod

    for _existing in _stdlib_logging.Logger.manager.loggerDict.values():
        if isinstance(_existing, _stdlib_logging.Logger):
            _existing.disabled = False

    structlog.reset_defaults()
    _log_mod._CONFIGURED = False
    configure_logging(config.logging)
    bind_run_id(run_id)
    log = structlog.get_logger("doctor")

    # --- Step 8: verify required tables exist post-migration ---
    conn = get_connection(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
    finally:
        conn.close()
    missing = _REQUIRED_TABLES - tables
    if missing:
        log.error("post_migration_tables_missing", missing=sorted(missing))
        typer.secho(
            f"ERROR: tables missing after migration: {sorted(missing)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=7)

    # --- Step 9: success ---
    log.info(
        "doctor_passed",
        run_id=run_id,
        db_path=str(db_path),
        broker_mode=config.broker.mode,
        anthropic_model=config.anthropic.model,
    )
    typer.secho("doctor passed", fg=typer.colors.GREEN)
    # Implicit exit code 0.


__all__ = ["doctor"]
