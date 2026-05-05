"""``meridian run-data`` CLI tests using Typer's CliRunner.

Mocks ``run_data_pipeline`` at ``ls_equity_fund.cli.data_cmd`` so the CLI
exit-code matrix can be exercised without invoking real refresh modules.
Coverage:
  - Exit 2 when --config missing.
  - Exit 3 when --env missing.
  - Exit 5 on --no-filings + --forms (mutually exclusive ValueError).
  - Exit 6 on Polygon provider (DATA-14 SystemExit from orchestrator).
  - Full pipeline: orchestrator called once with expected kwargs (defaults).
  - --no-filings forwards no_filings=True.
  - --forms parses comma-separated to list[str].
  - --help lists all six flags + exits 0.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from ls_equity_fund.cli.app import app

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_logging() -> None:
    """Reset structlog _CONFIGURED guard between tests so each invocation
    re-attaches handlers (mirrors test_cli_doctor.py pattern).
    """
    import ls_equity_fund.logging as _log

    _log._CONFIGURED = False
    yield
    _log._CONFIGURED = False


def _setup_workspace(tmp_path: Path) -> dict[str, Path]:
    """Provide a config.yaml + .env in tmp_path with cache_dir + log_dir
    repointed under tmp_path so the run is isolated.
    """
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text()
    yaml_text = yaml_text.replace(
        "cache_dir: cache", f"cache_dir: {tmp_path / 'cache'}"
    )
    yaml_text = yaml_text.replace(
        "log_dir: logs", f"log_dir: {tmp_path / 'logs'}"
    )
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(yaml_text)

    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test\n"
        "SEC_USER_AGENT=Test Operator test@example.com\n",
        encoding="utf-8",
    )
    return {"config": yaml_path, "env": env_path}


# ---------------------------------------------------------------------------
# --help surface contract
# ---------------------------------------------------------------------------


def test_run_data_help_lists_all_flags() -> None:
    result = runner.invoke(app, ["run-data", "--help"])
    assert result.exit_code == 0
    out = result.stdout
    for flag in (
        "--no-filings",
        "--no-13f",
        "--forms",
        "--ticker",
        "--universe-mode",
        "--config",
    ):
        assert flag in out, f"{flag!r} missing from --help output"


# ---------------------------------------------------------------------------
# Exit-code matrix
# ---------------------------------------------------------------------------


def test_run_data_exit_code_2_when_config_missing(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["run-data", "--config", str(tmp_path / "absent.yaml")],
    )
    assert result.exit_code == 2


def test_run_data_exit_code_3_when_env_missing(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    paths["env"].unlink()
    result = runner.invoke(
        app,
        [
            "run-data",
            "--config", str(paths["config"]),
            "--env", str(paths["env"]),
        ],
    )
    assert result.exit_code == 3


def test_run_data_exit_code_5_on_mutually_exclusive_flags(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    result = runner.invoke(
        app,
        [
            "run-data",
            "--config", str(paths["config"]),
            "--env", str(paths["env"]),
            "--no-filings",
            "--forms", "10-K",
        ],
    )
    assert result.exit_code == 5
    combined = (result.stdout or "") + (result.stderr or "")
    assert "mutually exclusive" in combined.lower()


def test_run_data_exit_code_6_on_polygon_provider(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    paths["config"].write_text(
        paths["config"]
        .read_text()
        .replace("provider: yfinance", "provider: polygon")
    )
    result = runner.invoke(
        app,
        [
            "run-data",
            "--config", str(paths["config"]),
            "--env", str(paths["env"]),
        ],
    )
    assert result.exit_code == 6
    combined = (result.stdout or "") + (result.stderr or "")
    assert "DATA-14" in combined


# ---------------------------------------------------------------------------
# Flag plumbing — orchestrator invocation contract
# ---------------------------------------------------------------------------


def test_run_data_full_pipeline_invokes_orchestrator(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
        orch.return_value = {
            "run_id": "abc1234567890",
            "duration_seconds": 1,
            "universe": 10,
            "filings": {"ok": 1},
            "institutional": {"ok": 1},
        }
        result = runner.invoke(
            app,
            [
                "run-data",
                "--config", str(paths["config"]),
                "--env", str(paths["env"]),
            ],
        )
    assert result.exit_code == 0, f"stderr: {result.stderr}"
    orch.assert_called_once()
    call_kwargs = orch.call_args.kwargs
    assert call_kwargs["no_filings"] is False
    assert call_kwargs["no_13f"] is False
    assert call_kwargs["forms"] is None
    assert call_kwargs["tickers"] is None


def test_run_data_no_filings_forwards_flag(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
        orch.return_value = {
            "run_id": "x",
            "duration_seconds": 0,
            "filings": None,
            "institutional": None,
            "universe": 0,
        }
        result = runner.invoke(
            app,
            [
                "run-data",
                "--config", str(paths["config"]),
                "--env", str(paths["env"]),
                "--no-filings",
            ],
        )
    assert result.exit_code == 0
    assert orch.call_args.kwargs["no_filings"] is True


def test_run_data_forms_parsed_to_list(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
        orch.return_value = {
            "run_id": "x",
            "duration_seconds": 0,
            "universe": 0,
        }
        result = runner.invoke(
            app,
            [
                "run-data",
                "--config", str(paths["config"]),
                "--env", str(paths["env"]),
                "--forms", "10-K, 10-Q",
            ],
        )
    assert result.exit_code == 0
    assert orch.call_args.kwargs["forms"] == ["10-K", "10-Q"]


def test_run_data_ticker_wraps_to_list(tmp_path: Path) -> None:
    paths = _setup_workspace(tmp_path)
    with patch("ls_equity_fund.cli.data_cmd.run_data_pipeline") as orch:
        orch.return_value = {
            "run_id": "x",
            "duration_seconds": 0,
            "universe": 0,
        }
        result = runner.invoke(
            app,
            [
                "run-data",
                "--config", str(paths["config"]),
                "--env", str(paths["env"]),
                "--ticker", "AAPL",
            ],
        )
    assert result.exit_code == 0
    assert orch.call_args.kwargs["tickers"] == ["AAPL"]
