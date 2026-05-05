"""End-to-end tests for `meridian run-scoring`."""

from __future__ import annotations

import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from typer.testing import CliRunner

from ls_equity_fund.cli.app import app

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_YAML = REPO_ROOT / "config.yaml.example"
EXAMPLE_ENV = REPO_ROOT / ".env.example"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _build_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Stage a runnable Meridian workspace under tmp_path.

    Returns (config_path, env_path, db_path).
    """
    config = tmp_path / "config.yaml"
    env = tmp_path / ".env"
    shutil.copy(EXAMPLE_YAML, config)
    shutil.copy(EXAMPLE_ENV, env)

    # Migrate a tmp DB
    cache = tmp_path / "cache"
    cache.mkdir()
    db_path = cache / "ls_equity_fund.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return config, env, db_path


def _seed_minimal(db_path: Path, sectors: dict[str, list[str]]) -> None:
    """Seed the universe + just enough data to keep most factors quiet (most return empty)."""
    asof = date(2026, 5, 5)
    now = int(time.time())
    conn = sqlite3.connect(db_path)
    with conn:
        for sector, tickers in sectors.items():
            for ticker in tickers:
                conn.execute(
                    "INSERT INTO universe (ticker, sector, first_seen_date, "
                    "inclusion_window, last_updated) VALUES (?, ?, ?, 'active', ?)",
                    (ticker, sector, asof.isoformat(), now),
                )
    conn.close()


def _seed_parent_scores(db_path: Path, asof: date) -> None:
    """Pre-populate factor_scores_parent so combined has data to consume."""
    base_factors = (
        "momentum",
        "value",
        "quality",
        "growth",
        "revisions",
        "short_interest",
        "insider",
        "institutional",
    )
    now = int(time.time())
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT ticker, sector FROM universe")
    universe = cur.fetchall()
    with conn:
        for i, (ticker, sector) in enumerate(universe):
            for f in base_factors:
                # Deterministic spread: lower-index ticker gets lower scores
                score = (i * 5.0 + hash(f) % 50) % 100
                conn.execute(
                    "INSERT INTO factor_scores_parent (ticker, score_date, factor, "
                    "parent_score, sector, n_subfactors_used, computed_at) "
                    "VALUES (?, ?, ?, ?, ?, 6, ?)",
                    (ticker, asof.isoformat(), f, score, sector, now),
                )
    conn.close()


def test_run_scoring_combined_only_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run only `combined` against pre-seeded parents → exit 0, runs row succeeded."""
    config, env, db_path = _build_workspace(tmp_path)
    _seed_minimal(db_path, {"IT": ["AAPL", "MSFT", "NVDA"], "Fin": ["JPM", "BAC", "GS"]})
    asof = date(2026, 5, 5)
    _seed_parent_scores(db_path, asof)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "run-scoring",
            "--config", str(config),
            "--env", str(env),
            "--asof", asof.isoformat(),
            "--factors", "combined",
            "--top", "5",
        ],
    )
    assert result.exit_code == 0, result.stdout

    # combined rows persisted
    conn = sqlite3.connect(db_path)
    n_combined = conn.execute(
        "SELECT COUNT(*) FROM factor_scores WHERE factor='combined' AND score_date=?",
        (asof.isoformat(),),
    ).fetchone()[0]
    assert n_combined == 6  # one per ticker

    # runs row exists with status='OK' (DB CHECK constrains to RUNNING/OK/FAILED)
    row = conn.execute(
        "SELECT status, end_ts, error FROM runs ORDER BY start_ts DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    status, end_ts, err = row
    assert status == "OK"
    assert end_ts is not None
    assert err is None  # clean run has no error annotation
    conn.close()


def test_run_scoring_dry_run_persists_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, env, db_path = _build_workspace(tmp_path)
    _seed_minimal(db_path, {"IT": ["AAPL", "MSFT"]})
    asof = date(2026, 5, 5)
    _seed_parent_scores(db_path, asof)

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "run-scoring",
            "--config", str(config),
            "--env", str(env),
            "--asof", asof.isoformat(),
            "--factors", "combined",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0

    conn = sqlite3.connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM factor_scores WHERE factor='combined'"
    ).fetchone()[0]
    assert n == 0
    # No runs row in dry-run either
    n_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert n_runs == 0
    conn.close()


def test_run_scoring_unknown_factor_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, env, _db = _build_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "run-scoring",
            "--config", str(config),
            "--env", str(env),
            "--factors", "no_such_factor",
        ],
    )
    assert result.exit_code == 5
    assert "unknown --factors" in (result.stderr or result.stdout)


def test_run_scoring_invalid_asof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, env, _db = _build_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["run-scoring", "--config", str(config), "--env", str(env), "--asof", "2026/05/05"],
    )
    assert result.exit_code == 5


def test_run_scoring_partial_when_factor_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When a factor raises mid-run, status='partial' and the orchestrator exits 7."""
    config, env, db_path = _build_workspace(tmp_path)
    _seed_minimal(db_path, {"IT": ["AAPL", "MSFT"]})
    asof = date(2026, 5, 5)

    monkeypatch.chdir(tmp_path)

    # Make every factor stub raise, simulating universal data gaps. The final
    # status should be 'partial' (since at least one factor fails) and the
    # orchestrator exits 7 (partial). combined will skip its own data load and
    # also fail (no parents), but the test focuses on the partial-status flow.
    def boom(*args: object, **kwargs: object) -> pd.DataFrame:
        raise RuntimeError("synthetic factor failure")

    with patch.dict(
        "ls_equity_fund.factors.composer.FACTOR_REGISTRY",
        {
            "momentum": boom, "value": boom, "quality": boom, "growth": boom,
            "revisions": boom, "short_interest": boom, "insider": boom,
            "institutional": boom, "combined": boom,
        },
    ):
        result = CliRunner().invoke(
            app,
            [
                "run-scoring",
                "--config", str(config),
                "--env", str(env),
                "--asof", asof.isoformat(),
            ],
        )

    assert result.exit_code == 7

    conn = sqlite3.connect(db_path)
    status, err = conn.execute(
        "SELECT status, error FROM runs ORDER BY start_ts DESC LIMIT 1"
    ).fetchone()
    # CHECK constrains to RUNNING/OK/FAILED; partial runs are persisted as OK
    # with a 'PARTIAL' marker in the error field.
    assert status == "OK"
    assert err is not None and err.startswith("PARTIAL")
    conn.close()
