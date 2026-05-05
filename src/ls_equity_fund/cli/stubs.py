"""Phase 0 stubs for the seven non-doctor CLI subcommands (D-23, INFRA-08).

Each stub:
  * Accepts the global flags it will eventually consume so flag wiring is
    complete from Phase 0 (``meridian run-portfolio --whatif --optimize-method
    mvo`` parses today even though MVO is Phase 7).
  * Prints ``"<cmd>: not implemented in this phase (Phase X)"`` and exits 0.
  * Lists which phase will replace the stub for operator-readable provenance.

Phase mapping:
  - ``daily-refresh``   -> Phase 10 (orchestrator); partial calls land earlier
  - ``run-data``        -> Phase 1
  - ``run-scoring``     -> Phase 2
  - ``run-analysis``    -> Phase 4
  - ``run-portfolio``   -> Phase 5 (conviction) / Phase 7 (mvo swap-in)
  - ``run-execution``   -> Phase 8
  - ``run-reporting``   -> Phase 3 (basic) / Phase 9 (full)

Per CONTEXT D-23 every flag declared here MUST be one of the locked v1 flags:
``--dry-run``, ``--whatif``, ``--no-filings``, ``--no-13f``, ``--ticker``,
``--sector``, ``--optimize-method``. Layer-specific extras (``--estimate-cost``)
are accepted only when explicitly called out by INFRA-08.
"""

from __future__ import annotations

import typer

_NOT_YET = "not implemented in this phase"


def daily_refresh(
    dry_run: bool = typer.Option(False, "--dry-run", help="(future) skip writes"),
    no_filings: bool = typer.Option(False, "--no-filings", help="(future) skip EDGAR filings"),
    no_13f: bool = typer.Option(False, "--no-13f", help="(future) skip 13F ingestion"),
) -> None:
    """Stub — meta-orchestrator chaining L1->L7. Phase 10 wires fully."""
    typer.echo(f"daily-refresh: {_NOT_YET} (Phase 10 orchestrator)")


def run_data(
    no_filings: bool = typer.Option(False, "--no-filings", help="(future) skip EDGAR"),
    no_13f: bool = typer.Option(False, "--no-13f", help="(future) skip 13F"),
    ticker: str | None = typer.Option(None, "--ticker", help="(future) restrict to one ticker"),
) -> None:
    """Stub — L1 data refresh. Phase 1 fills."""
    typer.echo(f"run-data: {_NOT_YET} (Phase 1)")


def run_scoring(
    ticker: str | None = typer.Option(None, "--ticker", help="(future) restrict to one ticker"),
    sector: str | None = typer.Option(
        None, "--sector", help="(future) restrict to one GICS sector"
    ),
) -> None:
    """Stub — L2 factor scoring. Phase 2 fills."""
    typer.echo(f"run-scoring: {_NOT_YET} (Phase 2)")


def run_analysis(
    ticker: str | None = typer.Option(None, "--ticker", help="(future) restrict to one ticker"),
    sector: str | None = typer.Option(
        None, "--sector", help="(future) restrict to one GICS sector"
    ),
    estimate_cost: bool = typer.Option(
        False, "--estimate-cost", help="(future) preview Claude spend without sending"
    ),
) -> None:
    """Stub — L3 Claude analyzers. Phase 4 fills."""
    typer.echo(f"run-analysis: {_NOT_YET} (Phase 4)")


def run_portfolio(
    whatif: bool = typer.Option(False, "--whatif", help="(future) preview rebalance only"),
    optimize_method: str = typer.Option(
        "conviction",
        "--optimize-method",
        help="(future) conviction | mvo",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="(future) skip writes"),
) -> None:
    """Stub — L4 portfolio construction. Phase 5 ships conviction; Phase 7 ships mvo."""
    typer.echo(f"run-portfolio: {_NOT_YET} (Phase 5/7) — optimize_method={optimize_method!r}")


def run_execution(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--execute",
        help="(future) dry preview vs real send",
    ),
) -> None:
    """Stub — L6 IBKR execution. Phase 8 fills."""
    typer.echo(f"run-execution: {_NOT_YET} (Phase 8) — dry_run={dry_run}")


def run_reporting(
    ticker: str | None = typer.Option(None, "--ticker", help="(future) restrict to one ticker"),
) -> None:
    """Stub — L7 reporting. Phase 3 ships basic; Phase 9 ships full."""
    typer.echo(f"run-reporting: {_NOT_YET} (Phase 3 basic / Phase 9 full)")


__all__ = [
    "daily_refresh",
    "run_analysis",
    "run_data",
    "run_execution",
    "run_portfolio",
    "run_reporting",
    "run_scoring",
]
