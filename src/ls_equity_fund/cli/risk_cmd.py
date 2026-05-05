"""``meridian run-risk`` CLI (Phase 6)."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from pathlib import Path

import pandas as pd
import typer

from ls_equity_fund.config import load_config
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.logging import configure_logging
from ls_equity_fund.portfolio.factor_exposure import compute_factor_exposure
from ls_equity_fund.portfolio.state import load_current_positions
from ls_equity_fund.risk.circuit_breaker import PortfolioState, evaluate_circuit_breakers
from ls_equity_fund.risk.factor_model import compute_factor_risk_model
from ls_equity_fund.risk.pre_trade_veto import load_recent_vetoes


def run_risk(
    whatif: bool = typer.Option(False, "--whatif", help="Preview risk state without writes"),
    asof: str | None = typer.Option(None, "--asof", help="Risk date YYYY-MM-DD; default today"),
    config_path: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml"),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Path to .env"),
    limit: int = typer.Option(20, "--limit", help="Rows of veto/breaker history to display"),
) -> None:
    """Show MCTR table, veto log, circuit status, and factor exposure warnings."""
    if not config_path.exists():
        typer.secho(f"ERROR: config.yaml not found at {config_path.resolve()}", err=True)
        raise typer.Exit(code=2)
    if not env_path.exists():
        typer.secho(f"ERROR: .env not found at {env_path.resolve()}", err=True)
        raise typer.Exit(code=3)
    config, _secrets = load_config(yaml_path=config_path, env_path=env_path)
    configure_logging(config.logging)
    asof_date: date_type = (
        date_type.today() if asof is None else datetime.strptime(asof, "%Y-%m-%d").date()
    )

    conn = get_connection(get_db_path(config))
    try:
        current = load_current_positions(conn)
        weights = _weights_from_positions(current, config.portfolio.target_aum_usd)
        risk = compute_factor_risk_model(
            conn,
            asof=asof_date,
            weights=weights,
            lookback=config.risk.factor_model_window_days,
        )
        typer.echo("MCTR")
        if risk.mctr.empty:
            typer.echo("(no positions or insufficient data)")
        else:
            typer.echo(risk.mctr.sort_values(key=lambda s: s.abs(), ascending=False).to_string())

        typer.echo("\nVeto log")
        vetoes = load_recent_vetoes(conn, limit=limit)
        typer.echo(vetoes.to_string(index=False) if not vetoes.empty else "(none)")

        state = PortfolioState(nav=config.portfolio.target_aum_usd)
        breakers = evaluate_circuit_breakers(state)
        typer.echo("\nCircuit breaker status")
        typer.echo("OK" if not breakers else "\n".join(f"{b.breaker_type}: {b.action}" for b in breakers))

        typer.echo("\nFactor exposure warnings")
        exposure = compute_factor_exposure(conn, weights=weights, asof=asof_date)
        if exposure.empty:
            typer.echo("(no exposure rows)")
        else:
            warnings = exposure[exposure["ls_spread"].abs() > 50]
            typer.echo(warnings.to_string() if not warnings.empty else "OK")

        if whatif:
            typer.echo("\nwhatif: no risk snapshots written")
    finally:
        conn.close()


def _weights_from_positions(current: pd.DataFrame, aum: float) -> pd.Series:
    if current.empty:
        return pd.Series(dtype=float)
    px = current["current_price"].fillna(current["entry_price"]).astype(float)
    values = current["shares"].astype(float) * px
    return pd.Series(values.to_numpy() / max(aum, 1e-9), index=current["ticker"])


__all__ = ["run_risk"]
