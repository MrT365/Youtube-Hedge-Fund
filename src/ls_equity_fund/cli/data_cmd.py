"""``meridian run-data`` — Phase 1 L1 daily-refresh CLI (DATA-12, DATA-14).

Replaces the Phase 0 stub. Mirrors the doctor scaffold:
    1. Locate config.yaml + .env (operator-friendly errors).
    2. load_config (pydantic validation).
    3. configure_logging.
    4. Delegate to run_data_pipeline (which generates run_id, binds it,
       writes runs row, chains 11 refresh steps).
    5. Print a summary banner with run_id + per-step counts.

Exit code map:
    0 — success
    2 — config.yaml not found
    3 — .env not found
    4 — config validation failed (pydantic error)
    5 — mutually-exclusive flag conflict (--no-filings + --forms)
    6 — provider guard rejection (DATA-14)
    7 — unexpected error escaping the orchestrator
"""
from __future__ import annotations

from pathlib import Path

import structlog
import typer

from ls_equity_fund.config import load_config
from ls_equity_fund.data.orchestrator import run_data_pipeline
from ls_equity_fund.logging import configure_logging


def run_data(
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
    no_filings: bool = typer.Option(
        False,
        "--no-filings",
        help="Skip 10-K/10-Q/8-K/Form 4 + 13F (DATA-12)",
    ),
    no_13f: bool = typer.Option(
        False,
        "--no-13f",
        help="Skip 13F only (DATA-12)",
    ),
    forms: str | None = typer.Option(
        None,
        "--forms",
        help=(
            "Comma-separated forms whitelist (e.g. '10-K,10-Q'); "
            "mutually exclusive with --no-filings"
        ),
    ),
    ticker: str | None = typer.Option(
        None,
        "--ticker",
        help="Restrict to one ticker (testing/dev override)",
    ),
    universe_mode: str | None = typer.Option(
        None,
        "--universe-mode",
        help="Override config.data.universe_mode (sp500|liquid_us|scanner_seed)",
    ),
) -> None:
    """Refresh L1 data: universe + benchmarks + prices + fundamentals + ratios + filings + 13F + short + estimates + earnings + macro."""
    # --- Step 1: locate config.yaml ---
    if not config_path.exists():
        typer.secho(
            f"ERROR: config.yaml not found at {config_path.resolve()}.\n"
            f"Hint: copy config.yaml.example to config.yaml and edit per-machine values.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    # --- Step 2: locate .env ---
    if not env_path.exists():
        typer.secho(
            f"ERROR: .env not found at {env_path.resolve()}.\n"
            f"Hint: copy .env.example to .env and fill required secrets.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=3)

    # --- Step 3: load_config (pydantic validation) ---
    try:
        config, secrets = load_config(yaml_path=config_path, env_path=env_path)
    except typer.Exit:
        raise
    except Exception as e:
        typer.secho(
            f"ERROR: failed to load config: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=4) from e

    # --- Step 4: configure logging ---
    configure_logging(config.logging)
    log = structlog.get_logger("run-data")

    forms_list = (
        [f.strip() for f in forms.split(",") if f.strip()] if forms else None
    )
    tickers_list = [ticker] if ticker else None

    # --- Step 5: delegate to orchestrator ---
    try:
        manifest = run_data_pipeline(
            config,
            secrets,
            no_filings=no_filings,
            no_13f=no_13f,
            forms=forms_list,
            tickers=tickers_list,
            universe_mode=universe_mode,
        )
    except ValueError as e:  # mutually-exclusive flags
        typer.secho(f"ERROR: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=5) from e
    except SystemExit as e:  # provider guard (DATA-14)
        msg = str(e) or "provider guard rejected this run"
        typer.secho(f"ERROR: {msg}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=6) from e
    except Exception as e:  # unexpected escapes the orchestrator's own try/except
        log.error("run_data_unexpected_failure", error=str(e))
        typer.secho(
            f"ERROR: pipeline failed: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=7) from e

    # --- Step 6: summary banner ---
    run_id_short = str(manifest.get("run_id", ""))[:8]
    duration = manifest.get("duration_seconds", 0)
    typer.secho(
        f"run-data complete (run_id={run_id_short}...) in {duration}s",
        fg=typer.colors.GREEN,
    )
    if manifest.get("filings") is None:
        typer.echo("  filings: SKIPPED")
    if manifest.get("institutional") is None:
        typer.echo("  13F:     SKIPPED")
    typer.echo(f"  universe rows: {manifest.get('universe')}")


__all__ = ["run_data"]
