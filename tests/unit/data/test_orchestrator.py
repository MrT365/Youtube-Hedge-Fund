"""run_data_pipeline orchestrator tests — uses fake step adapters.

Patches each `_*_step` adapter on the orchestrator module so the chain runs
without touching real refresh modules. Coverage:
  - Provider guard rejects non-yfinance with DATA-14 message.
  - --no-filings + --forms is mutually exclusive (ValueError).
  - Full pipeline calls all 11 step adapters.
  - --no-filings skips filings + 13F adapters.
  - --no-13f skips 13F adapter only.
  - --forms forwarded to filings adapter.
  - runs row lifecycle: INSERT 'RUNNING' at start, UPDATE 'OK' at clean exit.
  - Per-step failure does NOT abort the chain; subsequent steps still run.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.config import load_config
from ls_equity_fund.data.orchestrator import (
    DEFAULT_PHASE1_FORMS,
    SUPPORTED_PROVIDERS,
    run_data_pipeline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def setup(tmp_path: Path):
    """Build an isolated tmp project: alembic-migrated DB + loaded config + secrets."""
    # 1. Migrated SQLite DB at tmp_path/test.db (Phase 0 + Phase 1 schema).
    db = tmp_path / "test.db"
    alembic_cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option(
        "script_location", str(REPO_ROOT / "migrations")
    )
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    alembic_command.upgrade(alembic_cfg, "head")

    conn = sqlite3.connect(str(db))

    # 2. .env in tmp so Secrets validates.
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test\n"
        "SEC_USER_AGENT=Test Operator test@example.com\n",
        encoding="utf-8",
    )

    # 3. Config from repo example, cache_dir repointed to tmp.
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text()
    yaml_text = yaml_text.replace(
        "cache_dir: cache", f"cache_dir: {tmp_path / 'cache'}"
    )
    yaml_text = yaml_text.replace(
        "log_dir: logs", f"log_dir: {tmp_path / 'logs'}"
    )
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(yaml_text)

    config_obj, secrets_obj = load_config(
        yaml_path=yaml_path, env_path=env_path
    )

    yield config_obj, secrets_obj, conn
    conn.close()


# ---------------------------------------------------------------------------
# Module-level export contract
# ---------------------------------------------------------------------------


def test_supported_providers_is_yfinance_only() -> None:
    """v1 ships only yfinance; SUPPORTED_PROVIDERS guards DATA-14."""
    assert frozenset({"yfinance"}) == SUPPORTED_PROVIDERS


def test_default_phase1_forms_matches_filings_default() -> None:
    """Default forms list = ['10-K', '10-Q', '8-K', '4'] per Plan 06."""
    assert DEFAULT_PHASE1_FORMS == ["10-K", "10-Q", "8-K", "4"]


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


def test_provider_guard_rejects_non_yfinance(setup) -> None:
    config_obj, secrets, conn = setup
    config_obj.data.provider = "polygon"  # type: ignore[assignment]
    with pytest.raises(SystemExit, match="DATA-14"):
        run_data_pipeline(
            config_obj, secrets, conn=conn, today=date(2026, 4, 1)
        )


def test_no_filings_and_forms_mutually_exclusive(setup) -> None:
    config_obj, secrets, conn = setup
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_data_pipeline(
            config_obj,
            secrets,
            conn=conn,
            no_filings=True,
            forms=["10-K"],
            today=date(2026, 4, 1),
        )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


_STEP_NAMES = [
    "build_universe",
    "refresh_benchmarks",
    "refresh_prices",
    "refresh_fundamentals",
    "compute_ratios",
    "refresh_filings",
    "refresh_13f",
    "refresh_short_interest",
    "refresh_estimates",
    "refresh_earnings",
    "refresh_macro",
]


def _patch_all_steps(return_value=None):
    """Yield a list of started patches that stub every _*_step adapter."""
    patches = []
    mocks = {}
    for name in _STEP_NAMES:
        target = f"ls_equity_fund.data.orchestrator._{name}_step"
        rv = return_value if return_value is not None else {"ok": 1}
        p = patch(target, return_value=rv)
        patches.append(p)
        mocks[name] = p.start()
    return patches, mocks


def _stop_patches(patches) -> None:
    for p in patches:
        p.stop()


def test_full_pipeline_calls_all_eleven_steps(setup) -> None:
    config_obj, secrets, conn = setup
    patches, mocks = _patch_all_steps()
    try:
        manifest = run_data_pipeline(
            config_obj, secrets, conn=conn, today=date(2026, 4, 1)
        )
    finally:
        _stop_patches(patches)

    for name in _STEP_NAMES:
        assert mocks[name].called, f"step {name} was not called"
    assert "duration_seconds" in manifest
    assert "run_id" in manifest


def test_no_filings_skips_filings_and_13f(setup) -> None:
    config_obj, secrets, conn = setup
    patches, mocks = _patch_all_steps()
    try:
        manifest = run_data_pipeline(
            config_obj,
            secrets,
            conn=conn,
            no_filings=True,
            today=date(2026, 4, 1),
        )
    finally:
        _stop_patches(patches)

    mocks["refresh_filings"].assert_not_called()
    mocks["refresh_13f"].assert_not_called()
    assert manifest["filings"] is None
    assert manifest["institutional"] is None


def test_no_13f_skips_only_13f(setup) -> None:
    config_obj, secrets, conn = setup
    patches, mocks = _patch_all_steps()
    try:
        run_data_pipeline(
            config_obj,
            secrets,
            conn=conn,
            no_13f=True,
            today=date(2026, 4, 1),
        )
    finally:
        _stop_patches(patches)

    mocks["refresh_filings"].assert_called_once()
    mocks["refresh_13f"].assert_not_called()


def test_forms_passed_through_to_filings_step(setup) -> None:
    config_obj, secrets, conn = setup
    patches, mocks = _patch_all_steps()
    try:
        run_data_pipeline(
            config_obj,
            secrets,
            conn=conn,
            forms=["10-K"],
            today=date(2026, 4, 1),
        )
    finally:
        _stop_patches(patches)

    args, _kwargs = mocks["refresh_filings"].call_args
    # signature: (config, secrets, conn, forms, tickers, today)
    assert ["10-K"] in args, f"forms list not passed to filings step; args={args}"


def test_runs_row_lifecycle_ok(setup) -> None:
    """Successful run inserts then updates the runs row to status='OK'."""
    config_obj, secrets, conn = setup
    patches, _ = _patch_all_steps()
    try:
        manifest = run_data_pipeline(
            config_obj, secrets, conn=conn, today=date(2026, 4, 1)
        )
    finally:
        _stop_patches(patches)

    row = conn.execute(
        "SELECT status, end_ts, error FROM runs WHERE run_id=?",
        (manifest["run_id"],),
    ).fetchone()
    assert row is not None, "runs row missing after pipeline"
    assert row[0] == "OK"
    assert row[1] is not None  # end_ts populated
    assert row[2] is None  # no error


def test_step_failure_does_not_abort_chain(setup) -> None:
    """Per-step error is logged + recorded in manifest; remaining steps run."""
    config_obj, secrets, conn = setup
    target_prefix = "ls_equity_fund.data.orchestrator"

    with (
        patch(f"{target_prefix}._build_universe_step", side_effect=RuntimeError("boom")),
        patch(f"{target_prefix}._refresh_benchmarks_step", return_value={"ok": 1}) as bm,
        patch(f"{target_prefix}._refresh_prices_step", return_value={"ok": 1}),
        patch(f"{target_prefix}._refresh_fundamentals_step", return_value={"ok": 1}),
        patch(f"{target_prefix}._compute_ratios_step", return_value=10),
        patch(f"{target_prefix}._refresh_filings_step", return_value={"ok": 1}) as filings_mock,
        patch(f"{target_prefix}._refresh_13f_step", return_value={"ok": 1}),
        patch(f"{target_prefix}._refresh_short_interest_step", return_value={"ok": 1}),
        patch(f"{target_prefix}._refresh_estimates_step", return_value={"ok": 1}),
        patch(f"{target_prefix}._refresh_earnings_step", return_value={"ok": 1}),
        patch(f"{target_prefix}._refresh_macro_step", return_value={"ok": 1}),
    ):
        manifest = run_data_pipeline(
            config_obj, secrets, conn=conn, today=date(2026, 4, 1)
        )

    assert manifest["universe"] == {"error": "boom"}
    assert bm.called, "benchmark step did NOT run after universe failure"
    assert filings_mock.called, "filings step did NOT run after universe failure"

    row = conn.execute(
        "SELECT status FROM runs WHERE run_id=?", (manifest["run_id"],)
    ).fetchone()
    # Per-step failures are NOT promoted to runs.status='FAILED' — the chain
    # completed normally; runs.status='OK' reflects orchestrator-level success.
    assert row[0] == "OK"
