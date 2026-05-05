"""Typer app — single entry point for all ``meridian`` subcommands (D-23).

Subcommands wired here:
  * ``doctor`` — Phase 0 smoke check (this plan, ``cli/doctor.py``).
  * ``daily-refresh``, ``run-data``, ``run-scoring``, ``run-analysis``,
    ``run-portfolio``, ``run-execution``, ``run-reporting`` — Phase 0 stubs
    (``cli/stubs.py``); each accepts the global flags it will eventually
    consume per INFRA-08.

Per CONTEXT D-23 there is exactly ONE Typer instance and exactly ONE entry
point — both ``meridian`` (declared in pyproject.toml's ``[project.scripts]``)
and ``python -m ls_equity_fund.cli`` route through this app.
"""

from __future__ import annotations

import typer

from ls_equity_fund.cli.analysis_cmd import run_analysis as run_analysis_cmd
from ls_equity_fund.cli.data_cmd import run_data as run_data_cmd
from ls_equity_fund.cli.doctor import doctor as doctor_cmd
from ls_equity_fund.cli.portfolio_cmd import run_portfolio as run_portfolio_cmd
from ls_equity_fund.cli.scoring_cmd import run_scoring as run_scoring_cmd
from ls_equity_fund.cli.stubs import daily_refresh as daily_refresh_cmd
from ls_equity_fund.cli.stubs import (
    run_execution as run_execution_cmd,
)
from ls_equity_fund.cli.stubs import (
    run_reporting as run_reporting_cmd,
)

app = typer.Typer(
    name="meridian",
    help="Meridian Capital Partners — long/short equity fund CLI",
    no_args_is_help=True,
    pretty_exceptions_enable=False,  # let structlog/our handlers format errors
    add_completion=False,  # skip shell-completion subgroup; not relevant for solo operator
)

# Wire commands. Names use kebab-case per CLI convention (D-23).
app.command(
    "doctor",
    help="Smoke check: load config, open DB in WAL, apply migrations, exit 0.",
)(doctor_cmd)
app.command(
    "daily-refresh",
    help="(stub) Meta-orchestrator running L1->L7. Phase 10 fully wires.",
)(daily_refresh_cmd)
app.command(
    "run-data",
    help=(
        "Refresh L1 data: universe + benchmarks + prices + fundamentals + "
        "ratios + filings + 13F + short + estimates + earnings + macro."
    ),
)(run_data_cmd)
app.command(
    "run-scoring",
    help=(
        "Compute L2 factor scores: 8 factors x 27 sub-factors, "
        "sector-percentile rank, persist to factor_scores."
    ),
)(run_scoring_cmd)
app.command(
    "run-analysis",
    help=(
        "Run L3 Claude analyzers: filing, risk, insider, sector + recompute "
        "combined score (60%% quant + 40%% Claude). $25 ceiling enforced."
    ),
)(run_analysis_cmd)
app.command(
    "run-portfolio",
    help=(
        "Build L4 target book + rebalance. Phase 5 ships conviction; "
        "Phase 7 swaps in MVO behind the same Optimizer seam."
    ),
)(run_portfolio_cmd)
app.command(
    "run-execution",
    help="(stub) Send orders to broker. Phase 8 fills.",
)(run_execution_cmd)
app.command(
    "run-reporting",
    help="(stub) Generate L7 reports + letter. Phase 9 fills.",
)(run_reporting_cmd)


__all__ = ["app"]
