"""ANAL-12 — ``meridian run-analysis`` Phase 4 CLI.

Modes:
  - ``--estimate-cost``        Print fresh-cache cost forecast for the run; exit 0
  - ``--ticker AAPL``          Restrict to a single ticker
  - ``--sector "Information Technology"``  Restrict to one sector
  - default                    Full run over top-N candidates from
                                ``factor_scores_parent`` ``factor='combined'``

Persists Claude responses to ``analysis_results`` (cache + audit), ALSO writes
per-candidate markdown reports to ``output/reports_{timestamp}/``.

After analyzers complete, runs ANAL-09 ``compute_and_persist`` to overwrite
the Phase 2 combined parent score with the v2 60% quant + 40% Claude blend.

Exit codes:
  0  success
  2  config.yaml missing
  3  .env missing
  4  config load failed
  5  invalid CLI arg
  6  no scoring data on the requested asof
  7  partial — at least one analyzer failed; Claude data persisted for those
       that succeeded; combined-score still reflects what we have
  8  cost ceiling hit mid-run; partial results persisted
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import typer

from ls_equity_fund.analysis import (
    cache as analysis_cache,
)
from ls_equity_fund.analysis import (
    combined_score,
    earnings_call_analyzer,
    filing_analyzer,
    insider_analyzer,
    report_generator,
    risk_analyzer,
    sector_analyzer,
)
from ls_equity_fund.analysis.claude_client import ClaudeClient
from ls_equity_fund.analysis.cost_tracker import CostCeilingExceeded, CostTracker
from ls_equity_fund.config import load_config
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.logging import bind_run_id, configure_logging

ANALYZERS_PER_TICKER = (filing_analyzer, risk_analyzer, insider_analyzer, earnings_call_analyzer)


def run_analysis(
    config_path: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml"),
    env_path: Path = typer.Option(Path(".env"), "--env", help="Path to .env"),
    asof: str | None = typer.Option(
        None, "--asof", help="Score date (YYYY-MM-DD); default = latest score_date"
    ),
    ticker: str | None = typer.Option(None, "--ticker", help="Restrict to one ticker"),
    sector: str | None = typer.Option(None, "--sector", help="Restrict to one GICS sector"),
    top: int = typer.Option(
        40, "--top", help="Top-N candidates to analyze (default 40 per ANAL-config)"
    ),
    estimate_cost: bool = typer.Option(
        False, "--estimate-cost", help="Print cost forecast and exit (no Claude calls)"
    ),
    skip_combined: bool = typer.Option(
        False, "--skip-combined", help="Don't recompute combined-score after analyzers"
    ),
    skip_reports: bool = typer.Option(
        False, "--skip-reports", help="Don't write per-candidate markdown reports"
    ),
    skip_sector: bool = typer.Option(
        False, "--skip-sector", help="Skip sector analyzer (one call per sector)"
    ),
) -> None:
    """Compute Layer-3 Claude qualitative analysis and refresh the combined score."""
    if not config_path.exists():
        typer.secho(
            f"ERROR: config.yaml not found at {config_path.resolve()}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if not env_path.exists():
        typer.secho(f"ERROR: .env not found at {env_path.resolve()}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3)

    try:
        config, secrets = load_config(yaml_path=config_path, env_path=env_path)
    except Exception as exc:
        typer.secho(f"ERROR: failed to load config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=4) from exc

    configure_logging(config.logging)
    run_id = uuid.uuid4()
    bind_run_id(run_id)
    log = structlog.get_logger("run-analysis")

    try:
        asof_date: date_type = (
            _resolve_asof(asof, config_path, env_path)
            if asof is None
            else datetime.strptime(asof, "%Y-%m-%d").date()
        )
    except ValueError as exc:
        typer.secho(f"ERROR: invalid --asof {asof!r}; expected YYYY-MM-DD", err=True)
        raise typer.Exit(code=5) from exc

    db_path = get_db_path(config)
    conn = get_connection(db_path)

    # Resolve target tickers
    target_tickers = _resolve_target_tickers(
        conn, asof=asof_date, ticker=ticker, sector=sector, top=top
    )
    if not target_tickers:
        typer.secho(
            f"ERROR: no scored candidates found for asof={asof_date.isoformat()} "
            f"with ticker={ticker} sector={sector}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=6)

    sectors_in_scope = _resolve_target_sectors(conn, target_tickers, override=sector)

    log.info(
        "run_analysis_invoked",
        run_id=str(run_id),
        asof=asof_date.isoformat(),
        n_tickers=len(target_tickers),
        n_sectors=len(sectors_in_scope),
        ticker=ticker,
        sector=sector,
    )

    # --- estimate-cost mode --------------------------------------------------
    if estimate_cost:
        _print_cost_estimate(target_tickers, sectors_in_scope, skip_sector=skip_sector)
        raise typer.Exit(code=0)

    # --- live run -----------------------------------------------------------
    api_key = secrets.anthropic_api_key
    if not api_key:
        typer.secho(
            "ERROR: ANTHROPIC_API_KEY not set in .env",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=4)

    cost_tracker = CostTracker(ceiling_usd=config.anthropic.cost_ceiling_usd)
    client = ClaudeClient.create(
        api_key=api_key,
        model=config.anthropic.model,
        cost_tracker=cost_tracker,
    )

    per_ticker_results, partial = _run_per_ticker_analyzers(
        conn, client, target_tickers, asof_date, run_id=str(run_id), log=log
    )

    sector_results: dict[str, dict[str, Any]] = {}
    if not skip_sector and not _ceiling_hit(cost_tracker):
        for s in sectors_in_scope:
            try:
                out = sector_analyzer.analyze(
                    conn=conn,
                    client=client,
                    sector=s,
                    asof=asof_date,
                    run_id=str(run_id),
                )
                if out is not None:
                    sector_results[s] = out
            except CostCeilingExceeded:
                log.warning("ceiling_hit_during_sector", sector=s)
                partial = True
                break
            except Exception as exc:
                log.exception("sector_analyzer_failed", sector=s, error=str(exc))
                partial = True

    # Recompute combined score (ANAL-09)
    if not skip_combined:
        try:
            combined_score.compute_and_persist(conn, asof=asof_date)
        except Exception as exc:
            log.exception("combined_score_failed", error=str(exc))
            partial = True

    # Write per-candidate markdown reports (ANAL-10)
    reports_dir: Path | None = None
    if not skip_reports and per_ticker_results:
        reports_dir = report_generator.write_reports(
            conn,
            asof=asof_date,
            tickers=target_tickers,
        )

    # Evict stale rows (cache hygiene; ANAL-04 30-day TTL)
    analysis_cache.evict_expired(conn)

    _print_summary(
        per_ticker=per_ticker_results,
        sector=sector_results,
        cost_tracker=cost_tracker,
        reports_dir=reports_dir,
        asof=asof_date,
    )

    conn.close()
    if _ceiling_hit(cost_tracker):
        raise typer.Exit(code=8)
    if partial:
        raise typer.Exit(code=7)


# --- helpers ----------------------------------------------------------------


def _resolve_asof(
    asof_arg: str | None,
    config_path: Path,
    env_path: Path,
) -> date_type:
    """Resolve --asof to today if not given. We don't query 'latest' from the
    DB here because asof can be in the future (back-fill scenarios)."""
    return date_type.today()


def _resolve_target_tickers(
    conn: Any,
    *,
    asof: date_type,
    ticker: str | None,
    sector: str | None,
    top: int,
) -> list[str]:
    """Top-N tickers by combined parent_score on asof, with optional filters."""
    if ticker:
        # Single-ticker mode: just confirm it has a quant score on asof
        row = conn.execute(
            "SELECT 1 FROM factor_scores_parent "
            "WHERE ticker = ? AND score_date = ? AND factor = 'combined'",
            (ticker, asof.isoformat()),
        ).fetchone()
        return [ticker] if row else []

    sql_parts = [
        "SELECT ticker FROM factor_scores_parent",
        "WHERE score_date = ? AND factor = 'combined'",
    ]
    params: list[object] = [asof.isoformat()]
    if sector:
        sql_parts.append("AND sector = ?")
        params.append(sector)
    sql_parts.append("ORDER BY parent_score DESC NULLS LAST LIMIT ?")
    params.append(int(top))
    rows = conn.execute("\n".join(sql_parts), params).fetchall()
    return [r[0] for r in rows]


def _resolve_target_sectors(
    conn: Any, target_tickers: list[str], *, override: str | None
) -> list[str]:
    if override:
        return [override]
    if not target_tickers:
        return []
    placeholders = ",".join("?" * len(target_tickers))
    rows = conn.execute(
        f"SELECT DISTINCT sector FROM universe WHERE ticker IN ({placeholders})",
        target_tickers,
    ).fetchall()
    return sorted(r[0] for r in rows if r[0])


def _run_per_ticker_analyzers(
    conn: Any,
    client: ClaudeClient,
    tickers: list[str],
    asof: date_type,
    *,
    run_id: str,
    log: Any,
) -> tuple[dict[str, dict[str, dict[str, Any] | None]], bool]:
    """Returns (per_ticker_results, partial_flag).

    ``per_ticker_results[ticker]`` = {analyzer_type: response | None}
    ``partial_flag`` is True when at least one analyzer failed for any ticker.
    """
    per_ticker: dict[str, dict[str, dict[str, Any] | None]] = {}
    partial = False
    for tkr in tickers:
        per_ticker[tkr] = {}
        for module in ANALYZERS_PER_TICKER:
            kind = module.ANALYZER_TYPE
            try:
                if module is risk_analyzer:
                    out = module.analyze(conn=conn, client=client, ticker=tkr, run_id=run_id)
                else:
                    out = module.analyze(
                        conn=conn,
                        client=client,
                        ticker=tkr,
                        asof=asof,
                        run_id=run_id,
                    )
                per_ticker[tkr][kind] = out
            except CostCeilingExceeded:
                log.warning("ceiling_hit_during_per_ticker", ticker=tkr, analyzer=kind)
                partial = True
                return per_ticker, partial
            except Exception as exc:
                log.exception("analyzer_failed", ticker=tkr, analyzer=kind, error=str(exc))
                per_ticker[tkr][kind] = None
                partial = True
    return per_ticker, partial


def _ceiling_hit(tracker: CostTracker) -> bool:
    return tracker.total_usd >= tracker.ceiling_usd


def _print_cost_estimate(
    tickers: list[str],
    sectors: list[str],
    *,
    skip_sector: bool,
) -> None:
    n = len(tickers)
    estimates = {
        "filing": filing_analyzer.estimate_run_cost(n),
        "risk": risk_analyzer.estimate_run_cost(n),
        "insider": insider_analyzer.estimate_run_cost(n),
        "earnings_call": earnings_call_analyzer.estimate_run_cost(n),
    }
    if not skip_sector:
        estimates["sector"] = sector_analyzer.estimate_run_cost(len(sectors))
    total = sum(estimates.values())
    typer.secho(
        f"\nCost estimate (warm-cache) for {n} ticker(s), {len(sectors)} sector(s):",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  {'analyzer':16s}  {'estimate':>10s}")
    typer.echo(f"  {'-' * 16}  {'-' * 10}")
    for name, cost in estimates.items():
        typer.echo(f"  {name:16s}  ${cost:>9,.4f}")
    typer.echo(f"  {'-' * 16}  {'-' * 10}")
    color = typer.colors.GREEN if total < 25.0 else typer.colors.RED
    typer.secho(f"  {'TOTAL':16s}  ${total:>9,.4f}", fg=color, bold=True)


def _print_summary(
    *,
    per_ticker: dict[str, dict[str, dict[str, Any] | None]],
    sector: dict[str, dict[str, Any]],
    cost_tracker: CostTracker,
    reports_dir: Path | None,
    asof: date_type,
) -> None:
    typer.secho(
        f"\nrun-analysis summary — score_date {asof.isoformat()}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"\nPer-ticker analyzer outcomes ({len(per_ticker)} tickers):")
    typer.echo(f"  {'ticker':8s}  {'filing':>8s}  {'risk':>6s}  {'insider':>9s}")
    typer.echo(f"  {'-' * 8}  {'-' * 8}  {'-' * 6}  {'-' * 9}")
    for tkr in sorted(per_ticker):
        results = per_ticker[tkr]
        flags = {k: ("✓" if results.get(k) else "—") for k in ("filing", "risk", "insider")}
        typer.echo(
            f"  {tkr:8s}  {flags['filing']:>8s}  {flags['risk']:>6s}  {flags['insider']:>9s}"
        )

    if sector:
        typer.echo(f"\nSector analyses produced: {len(sector)}")
        for s in sorted(sector):
            stance = sector[s].get("outlook_stance", "—")
            long_t = (sector[s].get("top_long_idea") or {}).get("ticker", "—")
            short_t = (sector[s].get("top_short_idea") or {}).get("ticker", "—")
            typer.echo(f"  {s}: stance={stance}, long={long_t}, short={short_t}")

    summary = cost_tracker.summary()
    typer.secho("\nCost summary:", fg=typer.colors.CYAN)
    typer.echo(f"  calls:                {summary['calls']}")
    typer.echo(f"  input tokens:         {summary['input_tokens']:,}")
    typer.echo(f"  output tokens:        {summary['output_tokens']:,}")
    typer.echo(f"  cache_write tokens:   {summary['cache_write_tokens']:,}")
    typer.echo(f"  cache_read tokens:    {summary['cache_read_tokens']:,}")
    typer.echo(f"  cache_hit_rate:       {summary['cache_hit_rate']:.1%}")
    color = (
        typer.colors.GREEN if summary["total_usd"] < summary["ceiling_usd"] else typer.colors.RED
    )
    typer.secho(
        f"  TOTAL ${summary['total_usd']:.4f}  (ceiling ${summary['ceiling_usd']:.2f}, "
        f"remaining ${summary['remaining_usd']:.4f})",
        fg=color,
        bold=True,
    )

    if reports_dir is not None:
        typer.secho(f"\nReports written to: {reports_dir}", fg=typer.colors.CYAN)


__all__ = ["run_analysis"]
