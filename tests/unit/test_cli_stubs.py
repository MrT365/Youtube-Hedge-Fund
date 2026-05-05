"""Tests for the seven stub subcommands (D-23 — flag wiring, behavior is stubbed).

Each stub MUST:
  * Accept its locked global-flag set without 'unknown option' errors.
  * Print 'not implemented' to stdout.
  * Exit 0.

We also assert ``--help`` lists all 8 commands (doctor + 7 stubs).
"""

from __future__ import annotations

from typer.testing import CliRunner

from ls_equity_fund.cli.app import app

# Click 8.3+ dropped mix_stderr; stderr is separate via result.stderr automatically.
runner = CliRunner()


def test_help_lists_all_eight_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "doctor",
        "daily-refresh",
        "run-data",
        "run-scoring",
        "run-analysis",
        "run-portfolio",
        "run-execution",
        "run-reporting",
    ):
        assert cmd in result.stdout, f"{cmd!r} missing from --help output"


def test_daily_refresh_stub_accepts_flags() -> None:
    result = runner.invoke(app, ["daily-refresh", "--dry-run", "--no-filings", "--no-13f"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "not implemented" in result.stdout


def test_run_scoring_stub_accepts_flags() -> None:
    result = runner.invoke(app, ["run-scoring", "--ticker", "AAPL", "--sector", "Tech"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "not implemented" in result.stdout


def test_run_analysis_stub_accepts_flags() -> None:
    result = runner.invoke(
        app,
        ["run-analysis", "--ticker", "AAPL", "--sector", "Tech", "--estimate-cost"],
    )
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "not implemented" in result.stdout


def test_run_portfolio_stub_accepts_conviction() -> None:
    result = runner.invoke(app, ["run-portfolio", "--whatif", "--optimize-method", "conviction"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    # The stub echoes the optimize_method value back so we can assert flag wiring.
    assert "conviction" in result.stdout


def test_run_portfolio_stub_accepts_mvo() -> None:
    """Phase 7 MVO swap-in must parse today even though the body is stubbed."""
    result = runner.invoke(
        app,
        ["run-portfolio", "--whatif", "--optimize-method", "mvo", "--dry-run"],
    )
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "mvo" in result.stdout


def test_run_execution_stub_accepts_dry_run() -> None:
    result = runner.invoke(app, ["run-execution", "--dry-run"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "not implemented" in result.stdout


def test_run_execution_stub_accepts_execute() -> None:
    """``--execute`` is the negative of the ``--dry-run/--execute`` Typer toggle."""
    result = runner.invoke(app, ["run-execution", "--execute"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    # When --execute is passed, dry_run should resolve to False.
    assert "dry_run=False" in result.stdout


def test_run_reporting_stub_accepts_flags() -> None:
    result = runner.invoke(app, ["run-reporting", "--ticker", "AAPL"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "not implemented" in result.stdout


def test_unknown_flag_fails() -> None:
    """Negative test — unknown flag must NOT silently succeed (Typer default behavior)."""
    result = runner.invoke(app, ["run-portfolio", "--bogus-flag"])
    assert result.exit_code != 0, "unknown flags must error out"
