"""Tests for the remaining stub subcommands (D-23 flag wiring).

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


def test_run_analysis_estimate_cost_mode() -> None:
    """Phase 4 replaced the stub; --estimate-cost is the cheapest live mode."""
    result = runner.invoke(
        app,
        ["run-analysis", "--ticker", "AAPL", "--sector", "Tech", "--estimate-cost"],
    )
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    assert "Cost estimate" in result.stdout
    assert "TOTAL" in result.stdout


def test_run_portfolio_accepts_conviction_flag() -> None:
    """Phase 5 replaced the stub. Without config.yaml the command should
    accept the flags without 'unknown option' errors and fail at the missing-
    config gate (exit 2)."""
    result = runner.invoke(
        app,
        [
            "run-portfolio",
            "--whatif",
            "--optimize-method",
            "conviction",
            "--config",
            "/tmp/does-not-exist-meridian.yaml",
        ],
    )
    # The flags parse; failure mode is the missing-config gate, not a parse error.
    assert result.exit_code == 2, f"unexpected exit {result.exit_code}; stderr: {result.stderr}"


def test_run_portfolio_accepts_mvo_flag() -> None:
    """Phase 7 MVO swap-in. Phase 5 still needs to parse the flag — once the
    config gate clears (Phase 7), the body will raise NotImplementedError
    with exit 8. With a missing-config the command short-circuits at exit 2."""
    result = runner.invoke(
        app,
        [
            "run-portfolio",
            "--whatif",
            "--optimize-method",
            "mvo",
            "--dry-run",
            "--config",
            "/tmp/does-not-exist-meridian.yaml",
        ],
    )
    assert result.exit_code == 2, f"unexpected exit {result.exit_code}; stderr: {result.stderr}"


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
