"""``meridian run-scoring`` — Phase 2 L2 scoring orchestrator.

Loads config + secrets, opens the DB in WAL mode, and runs the 9 factor
modules registered in ``FACTOR_REGISTRY``. The 8 base factors run first; the
``combined`` composite runs last so it can read the freshly-persisted parent
scores.

For each factor we:
  1. Call the registered ``compute_<factor>(conn, asof, tickers)`` → long-form
     ``(ticker, sub_factor, raw_value)`` DataFrame.
  2. Attach ``sector`` via the universe table.
  3. Compute sector-percentile rank → ``percentile_rank`` and ``n_in_sector``.
  4. Stamp ``factor``, ``score_date``.
  5. Idempotent INSERT OR REPLACE into ``factor_scores``.
  6. Aggregate sub-factors → parent score → INSERT OR REPLACE into
     ``factor_scores_parent``.

After all 9 factors persist, we print a per-factor row-count summary, the
sectors covered, and the top-N ranked candidates by combined percentile.

A ``runs`` row is persisted at entry (status=running) and updated at exit
(status=succeeded/partial/failed). The same ``run_id`` UUID4 is bound to
structlog contextvars so every log line in the run carries it.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

import pandas as pd
import structlog
import typer

from ls_equity_fund.config import load_config
from ls_equity_fund.db import get_connection, get_db_path
from ls_equity_fund.factors import (
    BASE_FACTORS,
    FACTOR_NAMES,
    FACTOR_REGISTRY,
    compute_parent_factor_score,
    compute_sector_percentile_rank,
    write_factor_scores,
    write_parent_scores,
)
from ls_equity_fund.logging import bind_run_id, configure_logging


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
        help=f"Comma-separated subset of {FACTOR_NAMES}; default = all.",
    ),
    top: int = typer.Option(20, "--top", help="Print N top candidates by combined score"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Compute but do not persist; print row counts only.",
    ),
) -> None:
    """Compute L2 factor scores and persist to factor_scores / factor_scores_parent."""
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
    log = structlog.get_logger("run-scoring")

    # asof
    try:
        asof_date: date_type = (
            date_type.today() if asof is None else datetime.strptime(asof, "%Y-%m-%d").date()
        )
    except ValueError as exc:
        typer.secho(f"ERROR: invalid --asof {asof!r}; expected YYYY-MM-DD", err=True)
        raise typer.Exit(code=5) from exc

    # factor selection
    requested = (
        [name.strip() for name in factors.split(",")] if factors else list(FACTOR_NAMES)
    )
    unknown = [name for name in requested if name not in FACTOR_NAMES]
    if unknown:
        typer.secho(
            f"ERROR: unknown --factors values: {unknown}; expected subset of {FACTOR_NAMES}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=5)

    # Order: base factors first (in BASE_FACTORS order), combined last.
    base_to_run = [name for name in BASE_FACTORS if name in requested]
    composite_to_run = [name for name in requested if name not in BASE_FACTORS]
    factor_order = [*base_to_run, *composite_to_run]

    log.info(
        "run_scoring_invoked",
        run_id=str(run_id),
        asof=asof_date.isoformat(),
        ticker=ticker,
        sector=sector,
        factors=factor_order,
        dry_run=dry_run,
    )

    db_path = get_db_path(config)
    conn = get_connection(db_path)
    runs_row_id = str(run_id)
    if not dry_run:
        _open_runs_row(conn, runs_row_id, started_at=int(time.time()))

    try:
        target_tickers = _resolve_target_tickers(conn, ticker=ticker, sector=sector)
        if not target_tickers:
            typer.secho(
                "ERROR: no tickers matched --ticker/--sector filter and active universe is empty",
                fg=typer.colors.RED,
                err=True,
            )
            if not dry_run:
                _close_runs_row(conn, runs_row_id, status="failed", error="empty universe")
            raise typer.Exit(code=6)

        log.info("scoring_target_size", n_tickers=len(target_tickers))

        per_factor_summary: list[dict[str, object]] = []
        partial = False

        for factor_name in factor_order:
            try:
                rows_written = _score_one_factor(
                    conn,
                    factor_name=factor_name,
                    asof=asof_date,
                    target_tickers=target_tickers,
                    dry_run=dry_run,
                )
            except Exception as exc:  # log+continue per Phase 1 orchestrator pattern
                log.exception("factor_failed", factor=factor_name, error=str(exc))
                per_factor_summary.append(
                    {"factor": factor_name, "rows": 0, "status": "failed", "error": str(exc)}
                )
                partial = True
                continue
            per_factor_summary.append(
                {"factor": factor_name, "rows": rows_written, "status": "ok", "error": None}
            )

        # Final summary
        _print_summary(per_factor_summary, asof_date)
        if "combined" in factor_order and not dry_run:
            _print_top_candidates(conn, asof_date, top=top)

        final_status = "partial" if partial else "succeeded"
        if not dry_run:
            _close_runs_row(conn, runs_row_id, status=final_status, error=None)

        log.info("run_scoring_complete", status=final_status)
        if partial:
            raise typer.Exit(code=7)

    except typer.Exit:
        raise
    except Exception as exc:
        log.exception("run_scoring_fatal", error=str(exc))
        if not dry_run:
            _close_runs_row(conn, runs_row_id, status="failed", error=str(exc))
        typer.secho(f"ERROR: run-scoring fatal: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _resolve_target_tickers(
    conn: sqlite3.Connection,
    *,
    ticker: str | None,
    sector: str | None,
) -> list[str]:
    """Return the active (non-delisted) ticker list, optionally filtered."""
    query = "SELECT ticker FROM universe WHERE delisted_date IS NULL"
    params: list[str] = []
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker)
    if sector:
        query += " AND sector = ?"
        params.append(sector)
    query += " ORDER BY ticker"
    cur = conn.execute(query, params)
    return [row[0] for row in cur.fetchall()]


def _score_one_factor(
    conn: sqlite3.Connection,
    *,
    factor_name: str,
    asof: date_type,
    target_tickers: list[str],
    dry_run: bool,
) -> int:
    """Run one factor through the standard rank → persist pipeline.

    Returns the number of factor_scores rows written (0 in dry-run).
    """
    log = structlog.get_logger("run-scoring").bind(factor=factor_name)
    fn = FACTOR_REGISTRY[factor_name]

    log.info("factor_starting")
    raw_df = fn(conn, asof, target_tickers)

    if raw_df.empty:
        log.warning("factor_no_data")
        return 0

    # Attach sector from universe
    sector_df = pd.read_sql_query(
        "SELECT ticker, sector FROM universe WHERE delisted_date IS NULL",
        conn,
    )
    enriched = raw_df.merge(sector_df, on="ticker", how="inner")

    # Sector-percentile rank within (factor, sub_factor, sector) cohort.
    ranked_parts: list[pd.DataFrame] = []
    for _sub_factor, group in enriched.groupby("sub_factor", dropna=False):
        ranked = compute_sector_percentile_rank(group.copy())
        ranked_parts.append(ranked)
    ranked_df = pd.concat(ranked_parts, ignore_index=True) if ranked_parts else enriched

    ranked_df = ranked_df.assign(
        factor=factor_name,
        score_date=asof.isoformat(),
        sufficient_history=1,
    )

    if dry_run:
        log.info("factor_dry_run", n_rows=len(ranked_df))
        return 0

    n_written = write_factor_scores(conn, ranked_df)

    # Aggregate parent score
    parent_df = compute_parent_factor_score(ranked_df)
    if not parent_df.empty:
        write_parent_scores(conn, parent_df)

    log.info("factor_persisted", n_factor_scores=n_written, n_parent=len(parent_df))
    return n_written


# The runs.status CHECK accepts only ('RUNNING', 'OK', 'FAILED'). We expose
# friendlier 'partial' / 'succeeded' semantics in logs but persist OK for both
# clean + partial runs. A partial run still counts as completed; the per-factor
# error column on runs captures which step degraded.
_TERMINAL_STATUS_DB: dict[str, str] = {
    "succeeded": "OK",
    "partial": "OK",  # partial runs are persisted as OK; error field captures details
    "failed": "FAILED",
}


def _open_runs_row(conn: sqlite3.Connection, run_id: str, started_at: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO runs (run_id, start_ts, status) VALUES (?, ?, 'RUNNING')",
            (run_id, started_at),
        )


def _close_runs_row(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    error: str | None,
) -> None:
    db_status = _TERMINAL_STATUS_DB.get(status, "FAILED")
    # Annotate the error column with the human label when partial, so audit
    # readers can distinguish OK from OK-with-partial-failures.
    persisted_error = error
    if status == "partial":
        persisted_error = f"PARTIAL: {error}" if error else "PARTIAL"
    with conn:
        conn.execute(
            "UPDATE runs SET end_ts = ?, status = ?, error = ? WHERE run_id = ?",
            (int(time.time()), db_status, persisted_error, run_id),
        )


def _print_summary(per_factor: list[dict[str, object]], asof: date_type) -> None:
    """Print a per-factor row-count + status table to stdout."""
    typer.secho(
        f"\nFactor scoring summary — score_date {asof.isoformat()}",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  {'factor':20s}  {'status':10s}  {'rows':>8s}  notes")
    typer.echo(f"  {'-' * 20}  {'-' * 10}  {'-' * 8}  {'-' * 30}")
    for entry in per_factor:
        status = str(entry["status"])
        color = typer.colors.GREEN if status == "ok" else typer.colors.RED
        err_str = str(entry["error"] or "")[:30]
        typer.secho(
            f"  {entry['factor']:20s}  {status:10s}  {entry['rows']!s:>8s}  {err_str}",
            fg=color,
        )


def _print_top_candidates(
    conn: sqlite3.Connection, asof: date_type, *, top: int
) -> None:
    """Print top-N tickers by combined percentile_rank."""
    df = pd.read_sql_query(
        """
        SELECT ticker, sector, percentile_rank
        FROM factor_scores
        WHERE score_date = ? AND factor = 'combined' AND sub_factor = 'combined'
        ORDER BY percentile_rank DESC NULLS LAST
        LIMIT ?
        """,
        conn,
        params=(asof.isoformat(), top),
    )
    if df.empty:
        typer.secho("\n(no combined scores persisted)", fg=typer.colors.YELLOW)
        return

    sector_counts = df["sector"].value_counts().to_dict()
    typer.secho(
        f"\nTop {len(df)} candidates by combined percentile (sector-relative):",
        fg=typer.colors.CYAN,
        bold=True,
    )
    typer.echo(f"  {'rank':>4s}  {'ticker':8s}  {'sector':30s}  {'percentile':>10s}")
    typer.echo(f"  {'-' * 4}  {'-' * 8}  {'-' * 30}  {'-' * 10}")
    for i, row in enumerate(df.itertuples(index=False), start=1):
        pct = f"{row.percentile_rank:.2f}" if row.percentile_rank is not None else "—"
        typer.echo(f"  {i:>4d}  {row.ticker:8s}  {row.sector:30s}  {pct:>10s}")
    typer.secho(f"\nSector distribution: {sector_counts}", fg=typer.colors.CYAN)


__all__ = ["run_scoring"]
