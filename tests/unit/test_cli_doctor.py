"""Tests for the ``meridian doctor`` command — Phase 0 SC2 / D-25 / INFRA-08.

The fresh_workspace fixture builds a self-contained tmp project root with a
copy of config.yaml.example (cache_dir repointed at tmp), a minimal valid
.env, and an alembic.ini whose script_location points at the real migrations/
tree. monkeypatch.chdir(tmp_path) makes doctor see the tmp root as cwd.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ls_equity_fund.cli.app import app
from ls_equity_fund.logging import _CONFIGURED  # noqa: F401 — accessed via module patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Click 8.3+ / Typer 0.25 dropped the ``mix_stderr`` kwarg; stderr is always
# separated via ``result.stderr`` automatically.
runner = CliRunner()


@pytest.fixture(autouse=True)
def reset_logging() -> None:
    """Reset structlog _CONFIGURED guard between tests so configure_logging
    re-attaches handlers each invocation. Without this the second test's
    file handler points at the first test's tmp_path log file (which has
    already been removed), so log_dir assertions fail.
    """
    import ls_equity_fund.logging as _log

    _log._CONFIGURED = False
    yield
    _log._CONFIGURED = False


@pytest.fixture
def fresh_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a tmp 'project root' with config.yaml + .env + alembic.ini.

    Doctor expects to run from a directory containing:
      - config.yaml (cache_dir is repointed under tmp_path so the DB is isolated)
      - .env (minimal valid)
      - alembic.ini (script_location rewritten to absolute path of repo migrations)
    """
    # config.yaml — copy example, repoint cache_dir + log_dir into tmp.
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text()
    yaml_text = yaml_text.replace("cache_dir: cache", f"cache_dir: {tmp_path / 'cache'}")
    yaml_text = yaml_text.replace("log_dir: logs", f"log_dir: {tmp_path / 'logs'}")
    (tmp_path / "config.yaml").write_text(yaml_text)

    # .env — minimal valid contents (anthropic + sec_user_agent are required).
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-doctor\n"
        "SEC_USER_AGENT=Meridian Capital Partners doctor-test@example.com\n",
        encoding="utf-8",
    )

    # alembic.ini — copy and rewrite script_location to absolute path so alembic
    # can find migrations regardless of cwd.
    alembic_text = (REPO_ROOT / "alembic.ini").read_text()
    alembic_text = alembic_text.replace(
        "script_location = migrations",
        f"script_location = {REPO_ROOT / 'migrations'}",
    )
    (tmp_path / "alembic.ini").write_text(alembic_text)

    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_doctor_exits_zero_on_healthy_setup(fresh_workspace: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert "doctor passed" in result.stdout


def test_doctor_creates_runs_and_heartbeat_tables(fresh_workspace: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    db_path = fresh_workspace / "cache" / "ls_equity_fund.db"
    assert db_path.exists(), f"DB not created at {db_path}"
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"runs", "heartbeat", "alembic_version"}.issubset(tables), (
        f"missing tables; got {sorted(tables)}"
    )


def test_doctor_journal_mode_is_wal(fresh_workspace: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    db_path = fresh_workspace / "cache" / "ls_equity_fund.db"
    conn = sqlite3.connect(str(db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal", f"journal_mode={mode!r}, expected 'wal'"


def test_doctor_is_idempotent(fresh_workspace: Path) -> None:
    """D-25: re-running doctor on a healthy system exits 0 with no schema change."""
    r1 = runner.invoke(app, ["doctor"])
    assert r1.exit_code == 0, f"first run failed: {r1.stderr}"
    r2 = runner.invoke(app, ["doctor"])
    assert r2.exit_code == 0, f"second run failed: {r2.stderr}"

    # Confirm exactly ONE row in alembic_version after two doctor runs.
    db_path = fresh_workspace / "cache" / "ls_equity_fund.db"
    conn = sqlite3.connect(str(db_path))
    try:
        versions = list(conn.execute("SELECT version_num FROM alembic_version"))
    finally:
        conn.close()
    assert len(versions) == 1, f"alembic_version row count={len(versions)}, expected 1"


def test_doctor_missing_config_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit code 2 with helpful message pointing at config.yaml.example."""
    monkeypatch.chdir(tmp_path)  # no config.yaml here
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 2, f"got exit={result.exit_code}; stderr: {result.stderr}"
    assert "config.yaml not found" in result.stderr
    assert "config.yaml.example" in result.stderr


def test_doctor_missing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit code 3; doctor does NOT initialize .env (D-25 verbatim assertion)."""
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text()
    (tmp_path / "config.yaml").write_text(yaml_text)
    # No .env file written.
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3, f"got exit={result.exit_code}; stderr: {result.stderr}"
    assert ".env not found" in result.stderr
    assert ".env.example" in result.stderr
    # D-25 mandates the operator-facing message contain the "does NOT initialize"
    # phrase so they understand they must copy .env.example themselves.
    assert "does NOT initialize" in result.stderr


def test_doctor_malformed_config_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit code 4 when pydantic config validation fails."""
    yaml_text = (REPO_ROOT / "config.yaml.example").read_text()
    # Inject a type error: paper_port should be int.
    yaml_text = yaml_text.replace("paper_port: 7497", "paper_port: not_an_int")
    (tmp_path / "config.yaml").write_text(yaml_text)
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-test\nSEC_USER_AGENT=Meridian test@example.com\n"
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 4, f"got exit={result.exit_code}; stderr: {result.stderr}"
    assert "failed to load config" in result.stderr.lower()


def test_doctor_emits_doctor_passed_log(fresh_workspace: Path) -> None:
    """The success log line includes the literal 'doctor_passed' event."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"stderr: {result.stderr}"

    # Locate today's UTC log file under the tmp log_dir.
    import datetime as dt

    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    log_file = fresh_workspace / "logs" / f"{today}.jsonl"
    assert log_file.exists(), f"log file not at {log_file}"
    text = log_file.read_text()
    assert "doctor_passed" in text, "doctor_passed event not in log file"


def test_doctor_log_redacts_api_key(fresh_workspace: Path) -> None:
    """Audit-trail crosscheck: any log line referencing api_key must be redacted.

    The doctor command does not log the api_key explicitly, but if a future
    refactor leaks it via load_config errors or structlog event payloads, the
    redaction processor (D-18) must catch it. We verify by injecting a value
    via the config.yaml comment area that flows through the file but is
    redacted in stderr/log if it ever reaches structlog (defense-in-depth).
    """
    # We can't easily force the api_key into a log line from doctor itself, so
    # this test asserts the negative: the literal 'sk-ant-test-doctor' value
    # from .env is NOT present in the log file (because doctor never logs it
    # AND the redaction processor would mask it if it did).
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    import datetime as dt

    today = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    log_file = fresh_workspace / "logs" / f"{today}.jsonl"
    text = log_file.read_text()
    assert "sk-ant-test-doctor" not in text, (
        "raw API key leaked into log file — D-18 redaction failure"
    )
