"""Shared pytest fixtures for ls_equity_fund tests."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest
import structlog

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_YAML = REPO_ROOT / "config.yaml.example"
EXAMPLE_ENV = REPO_ROOT / ".env.example"


@pytest.fixture
def fresh_yaml_path(tmp_path: Path) -> Path:
    """Copy config.yaml.example into tmp_path/config.yaml and return its path."""
    dest = tmp_path / "config.yaml"
    shutil.copy(EXAMPLE_YAML, dest)
    return dest


@pytest.fixture
def fresh_env_path(tmp_path: Path) -> Path:
    """Write a known-good .env into tmp_path and return its path."""
    dest = tmp_path / ".env"
    dest.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-key-do-not-use\n"
        "IBKR_USERNAME=test_user\n"
        "IBKR_PASSWORD=test_pass\n"
        "SEC_USER_AGENT=Meridian Capital Partners test@example.com\n",
        encoding="utf-8",
    )
    return dest


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip nested-delimiter env vars and known-secret env vars between tests
    so leakage from the outer shell does not poison test isolation.
    """
    leaked_prefixes = ("BROKER__", "DATA__", "RISK__", "PORTFOLIO__", "ANTHROPIC__", "LOGGING__")
    leaked_secrets = (
        "ANTHROPIC_API_KEY",
        "IBKR_USERNAME",
        "IBKR_PASSWORD",
        "SEC_USER_AGENT",
    )
    for var in list(os.environ.keys()):
        if var.startswith(leaked_prefixes) or var in leaked_secrets:
            monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def isolate_logging() -> None:
    """Reset structlog + stdlib logging state between tests.

    `configure_logging()` mutates global structlog config and adds stdlib root
    handlers. Without reset, tests using `capsys` (e.g. macro_calendar) see
    stale handler configuration from earlier tests that called configure_logging.
    """
    structlog.reset_defaults()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
