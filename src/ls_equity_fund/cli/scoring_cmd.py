"""``meridian run-scoring`` Phase 2 CLI scaffold."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from pathlib import Path

import structlog
import typer

from ls_equity_fund.config import load_config
from ls_equity_fund.factors.composer import FACTOR_NAMES
from ls_equity_fund.logging import configure_logging


def run_scoring(
    config_path: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml"),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Path to .env"),
    asof: str | None = typer.Option(
        None,
        "--asof",
        help="Score date (YYYY-MM-DD); default is today.",
    ),
    ticker: str | None = typer.Option(None, "--ticker", help="Restrict to one ticker"),
    sector: str | None = typer.Option(None, "--sector", help="Restrict to one GICS sector"),
    factors: str | None = typer.Option(
        None,
        "--factors",
        help=f"Comma-separated subset of {FACTOR_NAMES}; default is all.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute but do not persist"),
) -> None:
    """Compute L2 factor scores."""
    if not config_path.exists():
        typer.secho(
            f"ERROR: config.yaml not found at {config_path.resolve()}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if not env_path.exists():
        typer.secho(
            f"ERROR: .env not found at {env_path.resolve()}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=3)

    try:
        config, _secrets = load_config(yaml_path=config_path, env_path=env_path)
    except Exception as exc:
        typer.secho(f"ERROR: failed to load config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=4) from exc

    configure_logging(config.logging)
    log = structlog.get_logger("run-scoring")

    try:
        asof_date: date_type = (
            date_type.today() if asof is None else datetime.strptime(asof, "%Y-%m-%d").date()
        )
    except ValueError as exc:
        typer.secho(f"ERROR: invalid --asof {asof!r}; expected YYYY-MM-DD", err=True)
        raise typer.Exit(code=5) from exc

    factor_names = [f.strip() for f in factors.split(",")] if factors else list(FACTOR_NAMES)
    unknown = [f for f in factor_names if f not in FACTOR_NAMES]
    if unknown:
        typer.secho(
            f"ERROR: unknown --factors values: {unknown}; expected subset of {FACTOR_NAMES}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=5)

    log.info(
        "run_scoring_invoked",
        asof=asof_date.isoformat(),
        ticker=ticker,
        sector=sector,
        factors=factor_names,
        dry_run=dry_run,
    )
    typer.secho(
        "run-scoring: orchestrator pending Plan 02-10. Skeleton only.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=97)


__all__ = ["run_scoring"]
