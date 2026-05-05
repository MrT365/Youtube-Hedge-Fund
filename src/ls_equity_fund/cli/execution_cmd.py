"""``meridian run-execution`` — Phase 8 paper execution."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import typer

from ls_equity_fund.config import load_config
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.execution.order_executor import OrderExecutor, OrderPlan
from ls_equity_fund.execution.paper_broker import PaperBroker
from ls_equity_fund.portfolio.state import load_current_positions
from ls_equity_fund.risk.pre_trade_veto import VetoContext


def run_execution(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--execute",
        help="Preview orders or place them through IBKR paper.",
    ),
    config_path: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml"),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Path to .env"),
    run_id: str | None = typer.Option(None, "--run-id", help="position_approvals run_id to execute"),
) -> None:
    try:
        config, _secrets = load_config(config_path, env_path=env_path)
    except FileNotFoundError as exc:
        typer.secho(f"ERROR: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    conn = get_connection(get_db_path(config))
    try:
        approvals = load_execution_approvals(conn, run_id=run_id)
        if approvals.empty:
            typer.secho("No approved portfolio orders to execute", fg=typer.colors.YELLOW)
            return

        broker = PaperBroker()
        executor = OrderExecutor(
            broker=broker,
            conn=conn,
            execution_cfg=config.execution,
            portfolio_cfg=config.portfolio,
        )
        context = VetoContext(
            aum_usd=config.portfolio.target_aum_usd,
            current_positions=load_current_positions(conn),
            max_net_beta=10.0,
        )
        plans = executor.build_plan(approvals, context=context)
        execution_run_id = run_id or str(uuid.uuid4())
        _print_plan(plans, dry_run=dry_run)
        executor.execute_plan(plans, run_id=execution_run_id, dry_run=dry_run)
    finally:
        conn.close()


def load_execution_approvals(conn: sqlite3.Connection, *, run_id: str | None = None) -> pd.DataFrame:
    if run_id is None:
        row = conn.execute(
            "SELECT run_id FROM position_approvals ORDER BY decided_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return pd.DataFrame()
        run_id = str(row[0])
    approvals = pd.read_sql_query(
        """
        SELECT pa.*, pa.limit_price AS signal_price, dp.adv_usd AS adv_usd
        FROM position_approvals pa
        LEFT JOIN (
            SELECT ticker, AVG(close * volume) AS adv_usd
            FROM daily_prices
            GROUP BY ticker
        ) dp ON dp.ticker = pa.ticker
        WHERE pa.run_id = ?
        ORDER BY ABS(pa.target_dollar) DESC
        """,
        conn,
        params=[run_id],
    )
    return approvals


def _print_plan(plans: Sequence[OrderPlan], *, dry_run: bool) -> None:
    typer.secho("\nExecution plan", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"mode={'dry-run' if dry_run else 'execute'}")
    for plan in plans:
        ticker = plan.ticker
        side = plan.side
        shares = plan.shares
        limit_price = plan.limit_price
        tif = plan.tif
        chunk_index = plan.chunk_index
        chunk_total = plan.chunk_total
        deferred = plan.deferred_reason
        suffix = f" DEFERRED={deferred}" if deferred else ""
        typer.echo(
            f"{ticker:>8} {side:<13} shares={shares:.2f} limit={limit_price:.2f} "
            f"TIF={tif} chunk={chunk_index}/{chunk_total}{suffix}"
        )


__all__ = ["load_execution_approvals", "run_execution"]
