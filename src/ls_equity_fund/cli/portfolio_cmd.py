"""``meridian run-portfolio`` — Phase 5 L4 orchestrator.

Modes:
  --optimize-method conviction  (default) — ships Phase 5
  --optimize-method mvo                    — Phase 7 SLSQP MVO with
                                              conviction fallback
  --whatif                                 — preview only; do not persist current
                                              positions, do persist position_approvals
                                              + portfolio_history snapshot for audit
  --asof YYYY-MM-DD                        — score date to read (default today)

Output to stdout:
  * Selected candidates: 20 longs / 20 shorts (or num_longs / num_shorts)
  * Per-position table with conviction tilt bucket + ADV cap + earnings halve flags
  * Portfolio aggregates: gross / net / book betas / sector net
  * Rebalance trade list (sorted by priority desc) with cost decomposition
  * Schedule advisory warnings (earnings / FOMC / opex)

Exit codes:
  0  success
  2  config.yaml missing
  3  .env missing
  4  config load failed
  5  invalid CLI arg
  6  no L2 scoring data on the asof
  7  partial — at least one downstream piece (e.g. beta) failed but trades emitted
  8  optimizer failed
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import structlog
import typer

from ls_equity_fund.config import PortfolioConfig, load_config
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.logging import bind_run_id, configure_logging
from ls_equity_fund.portfolio.beta import compute_betas
from ls_equity_fund.portfolio.conviction_tilt import (
    ConvictionTiltResult,
    build_target_book,
    load_candidate_frame,
)
from ls_equity_fund.portfolio.factor_exposure import compute_factor_exposure
from ls_equity_fund.portfolio.mvo import MVOResult, build_mvo_or_fallback
from ls_equity_fund.portfolio.rebalance import generate_rebalance
from ls_equity_fund.portfolio.schedule import evaluate_schedule
from ls_equity_fund.portfolio.state import (
    load_current_positions,
    write_portfolio_history,
    write_position_approvals,
)
from ls_equity_fund.risk.factor_model import compute_factor_risk_model
from ls_equity_fund.risk.pre_trade_veto import (
    TradeRequest,
    VetoContext,
    evaluate_pre_trade_veto,
)


def run_portfolio(
    config_path: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml"),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Path to .env"),
    optimize_method: str | None = typer.Option(
        None,
        "--optimize-method",
        help="conviction | mvo. Defaults to config.yaml portfolio.optimizer.",
    ),
    whatif: bool = typer.Option(
        False,
        "--whatif",
        help="Preview only — does not update current positions table.",
    ),
    asof: str | None = typer.Option(
        None,
        "--asof",
        help="Score date (YYYY-MM-DD); default is today.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Compute but do not persist position_approvals or history.",
    ),
) -> None:
    """Construct the target book + emit rebalance trades."""
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
    run_id = uuid.uuid4()
    bind_run_id(run_id)
    log = structlog.get_logger("run-portfolio")

    try:
        asof_date: date_type = (
            date_type.today() if asof is None else datetime.strptime(asof, "%Y-%m-%d").date()
        )
    except ValueError as exc:
        typer.secho(
            f"ERROR: invalid --asof {asof!r}; expected YYYY-MM-DD",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=5) from exc

    selected_optimizer = optimize_method or config.portfolio.optimizer
    if selected_optimizer not in ("conviction", "mvo"):
        typer.secho(
            f"ERROR: optimizer must be conviction|mvo, got {selected_optimizer!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=5)

    log.info(
        "run_portfolio_invoked",
        run_id=str(run_id),
        asof=asof_date.isoformat(),
        optimize_method=selected_optimizer,
        whatif=whatif,
        dry_run=dry_run,
    )

    db_path = get_db_path(config)
    conn = get_connection(db_path)
    try:
        candidates = load_candidate_frame(
            conn,
            asof=asof_date,
            earnings_window_days=config.portfolio.earnings_halve_window_days,
            adv_lookback=config.portfolio.adv_lookback_days,
        )
        if candidates.empty:
            typer.secho(
                f"ERROR: no combined factor scores found for asof={asof_date}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=6)

        log.info("loaded_candidates", n=len(candidates))

        betas = compute_betas(
            conn,
            tickers=candidates["ticker"].tolist(),
            asof=asof_date,
            lookback=config.portfolio.beta_lookback_days,
        )
        log.info("computed_betas", n=len(betas))

        result: ConvictionTiltResult | MVOResult
        if selected_optimizer == "mvo":
            initial_weights = pd.Series(0.0, index=candidates["ticker"].tolist())
            risk_result = compute_factor_risk_model(
                conn,
                asof=asof_date,
                weights=initial_weights,
                lookback=config.risk.factor_model_window_days,
            )
            result = build_mvo_or_fallback(
                conn,
                candidates,
                cfg=config.portfolio,
                covariance=risk_result.predicted_covariance,
                betas=betas,
                target_aum_usd=config.portfolio.target_aum_usd,
            )
            if result.used_fallback:
                typer.secho(
                    f"MVO fallback used: {result.fallback_reason}",
                    fg=typer.colors.YELLOW,
                )
        else:
            result = build_target_book(
                candidates,
                cfg=config.portfolio,
                betas=betas,
                target_aum_usd=config.portfolio.target_aum_usd,
            )
        if result.targets.empty:
            typer.secho(
                "ERROR: optimiser produced an empty target book",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=6)

        # Surface the audit columns in run-portfolio's stdout.
        _run_veto_preview(conn, result.targets, config.portfolio, current_positions=load_current_positions(conn))
        _print_target_book(result, config.portfolio.gross_target, selected_optimizer)

        current = load_current_positions(conn)
        trades, summary = generate_rebalance(
            targets=result.targets.assign(
                adv_usd=candidates.set_index("ticker")
                .reindex(result.targets["ticker"])["adv_usd"]
                .values
            ),
            current=current,
            cfg=config.portfolio,
            target_aum_usd=config.portfolio.target_aum_usd,
        )
        _print_rebalance(trades, summary, cfg_turnover=config.portfolio.turnover_budget)

        advisories = evaluate_schedule(
            conn,
            asof=asof_date,
            candidate_tickers=result.targets["ticker"].tolist(),
        )
        _print_advisories(advisories)

        # PORT-08: per-factor long/short exposure spread.
        weight_series = result.targets.set_index("ticker")["final_weight"]
        exposure = compute_factor_exposure(conn, weights=weight_series, asof=asof_date)
        _print_factor_exposure(exposure)

        if not dry_run:
            approval_rows = result.targets.copy()
            approval_rows["beta"] = approval_rows["ticker"].map(lambda t: betas.get(t))
            n_approvals = write_position_approvals(
                conn,
                run_id=str(run_id),
                asof=asof_date,
                rows=approval_rows.to_dict(orient="records"),
                optimizer=selected_optimizer,
            )
            log.info("wrote_position_approvals", n=n_approvals)

            # Snapshot a portfolio_history aggregate row so the dashboard can
            # surface gross/net/beta even before live positions exist.
            agg = {
                "gross_exposure": result.gross,
                "net_exposure": result.net,
                "net_beta": result.book_beta.net_beta,
                "long_book_beta": result.book_beta.long_book_beta,
                "short_book_beta": result.book_beta.short_book_beta,
            }
            per_pos = []
            for _, row in result.targets.iterrows():
                ticker = row["ticker"]
                shares = float(row.get("final_shares") or 0.0)
                price = float(row.get("limit_price") or 0.0)
                per_pos.append(
                    {
                        "ticker": ticker,
                        "side": row["side"],
                        "shares": shares,
                        "mark_price": price,
                        "market_value": shares * price,
                        "weight": float(row["final_weight"]),
                        "unrealized_pnl": 0.0,  # whatif → no entry yet
                        "beta": betas.get(ticker),
                        "sector": row.get("sector"),
                    }
                )
            n_history = write_portfolio_history(
                conn,
                asof=asof_date,
                per_position_rows=per_pos,
                aggregate=agg,
            )
            log.info("wrote_portfolio_history", n=n_history)

        log.info(
            "run_portfolio_complete",
            n_targets=len(result.targets),
            n_trades=summary.n_trades,
            net_beta=result.book_beta.net_beta,
            gross=result.gross,
        )
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Pretty-printing
# -----------------------------------------------------------------------------


def _run_veto_preview(
    conn: Any,
    targets: pd.DataFrame,
    portfolio_cfg: PortfolioConfig,
    *,
    current_positions: pd.DataFrame,
) -> None:
    """Evaluate Phase 6 veto code path for each target without blocking whatif output."""
    context = VetoContext(
        aum_usd=portfolio_cfg.target_aum_usd,
        current_positions=current_positions,
        max_net_beta=10.0,
    )
    for _, row in targets.iterrows():
        shares = float(row.get("final_shares") or 0.0)
        evaluate_pre_trade_veto(
            conn,
            trade=TradeRequest(
                ticker=str(row["ticker"]),
                side=str(row["side"]),
                shares=shares,
                price=float(row.get("limit_price") or row.get("price") or 0.0),
                sector=row.get("sector"),
                beta=row.get("beta"),
                adv_20d_usd=row.get("adv_usd"),
            ),
            context=context,
            portfolio_cfg=portfolio_cfg,
            persist=False,
        )


def _print_target_book(result: ConvictionTiltResult | MVOResult, gross_target: float, optimizer: str) -> None:
    typer.secho(
        f"\nTarget book ({optimizer})",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(
        f"  longs={int((result.targets['side'] == 'long').sum())}  "
        f"shorts={int((result.targets['side'] == 'short').sum())}  "
        f"gross={result.gross:.2%} (target {gross_target:.0%})  "
        f"net={result.net:+.2%}  "
        f"net_beta={result.book_beta.net_beta:+.3f}  "
        f"long_beta={result.book_beta.long_book_beta:+.3f}  "
        f"short_beta={result.book_beta.short_book_beta:+.3f}"
    )
    show = result.targets[
        [
            "ticker",
            "side",
            "sector",
            "tilt_bucket",
            "score",
            "earnings_halved",
            "final_weight",
            "final_shares",
            "target_dollar",
        ]
    ].copy()
    show["score"] = show["score"].round(2)
    show["final_weight"] = show["final_weight"].round(4)
    show["target_dollar"] = show["target_dollar"].round(0)
    typer.echo(show.to_string(index=False))

    if result.sector_net:
        typer.secho("\nSector-net (long − short):", fg=typer.colors.CYAN)
        for sec, net in sorted(result.sector_net.items(), key=lambda kv: -abs(kv[1])):
            typer.echo(f"  {sec:30s}  {net:+.4f}")


def _print_rebalance(trades: pd.DataFrame, summary, cfg_turnover: float) -> None:  # type: ignore[no-untyped-def]
    typer.secho(
        f"\nRebalance ({summary.n_trades} trades; "
        f"raw turnover {summary.raw_turnover:.1%} → "
        f"final {summary.final_turnover:.1%}; budget {cfg_turnover:.0%}; "
        f"dropped {summary.dropped_for_budget})",
        fg=typer.colors.CYAN,
        bold=True,
    )
    if trades.empty:
        typer.echo("  (no trades — book already at target)")
        return
    show = trades[
        [
            "ticker",
            "side",
            "action",
            "delta_shares",
            "trade_value",
            "commission_usd",
            "spread_usd",
            "impact_usd",
            "total_cost_usd",
            "total_cost_bps",
            "priority",
            "dropped_for_budget",
        ]
    ].copy()
    for col in (
        "delta_shares",
        "trade_value",
        "commission_usd",
        "spread_usd",
        "impact_usd",
        "total_cost_usd",
    ):
        show[col] = show[col].round(2)
    show["total_cost_bps"] = show["total_cost_bps"].round(1)
    show["priority"] = show["priority"].round(2)
    typer.echo(show.to_string(index=False))
    typer.secho(
        f"  total trade value ${summary.total_trade_value:,.0f}  "
        f"total cost ${summary.total_cost_usd:,.2f}",
        fg=typer.colors.CYAN,
    )


def _print_factor_exposure(exposure: pd.DataFrame) -> None:
    typer.secho("\nFactor exposure (long_avg / short_avg / spread)", fg=typer.colors.CYAN, bold=True)
    if exposure.empty:
        typer.echo("  (no factor_scores_parent rows for this asof)")
        return
    show = exposure.copy().reset_index()
    for col in ("long_avg", "short_avg", "ls_spread"):
        show[col] = show[col].round(2)
    typer.echo(show.to_string(index=False))


def _print_advisories(adv) -> None:  # type: ignore[no-untyped-def]
    typer.secho(
        f"\nSchedule advisories ({len(adv.warnings)} warnings — DOES NOT BLOCK)",
        fg=typer.colors.CYAN,
        bold=True,
    )
    if not adv.items:
        typer.echo("  (clear — no earnings/FOMC/opex within window)")
        return
    for a in adv.items:
        color = typer.colors.YELLOW if a.severity == "warn" else typer.colors.WHITE
        typer.secho(f"  [{a.code}] {a.message}", fg=color)


__all__ = ["run_portfolio"]
