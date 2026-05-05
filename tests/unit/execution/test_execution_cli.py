from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from typer.testing import CliRunner

from ls_equity_fund.cli.app import app

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _alembic_cfg(db_path: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _seed_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "exec_cli.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    conn = sqlite3.connect(str(db_path))
    now = int(time.time())
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO position_approvals (
                    run_id, asof_date, ticker, side, optimizer, tilt_bucket,
                    base_weight, tilted_weight, adv_capped_weight,
                    earnings_halved, beta_adjusted_weight, final_weight,
                    final_shares, target_dollar, limit_price, score, sector,
                    beta, advisory_flags, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run-cli",
                    "2026-05-01",
                    "AAA",
                    "long",
                    "conviction",
                    "top",
                    0.01,
                    0.01,
                    0.01,
                    0,
                    0.01,
                    0.01,
                    100,
                    10_000,
                    100.0,
                    95.0,
                    "Tech",
                    0.0,
                    "[]",
                    now,
                ),
            )
            for i in range(20):
                conn.execute(
                    """
                    INSERT INTO daily_prices (
                        ticker, date, open, high, low, close, adj_close, volume
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("AAA", f"2026-04-{i + 1:02d}", 100, 100, 100, 100, 100, 1_000_000),
                )
    finally:
        conn.close()
    return db_path


def _config(tmp_path: Path, db_path: Path) -> tuple[Path, Path]:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    shutil.copy(db_path, cache_dir / "ls_equity_fund.db")
    yaml = tmp_path / "config.yaml"
    text = (REPO_ROOT / "config.yaml.example").read_text(encoding="utf-8")
    yaml.write_text(text.replace("cache_dir: cache", f"cache_dir: {cache_dir}"), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "IBKR_USERNAME=test\n"
        "IBKR_PASSWORD=test\n"
        "SEC_USER_AGENT=Meridian test\n",
        encoding="utf-8",
    )
    return yaml, env


def test_run_execution_dry_run_shows_per_ticker_plan(tmp_path: Path) -> None:
    yaml, env = _config(tmp_path, _seed_db(tmp_path))
    result = runner.invoke(
        app,
        ["run-execution", "--dry-run", "--config", str(yaml), "--env", str(env)],
    )
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "Execution plan" in result.stdout
    assert "AAA" in result.stdout
    assert "TIF=DAY" in result.stdout
    assert "chunk=1/1" in result.stdout


def test_run_execution_execute_records_required_order_fields(tmp_path: Path) -> None:
    db = _seed_db(tmp_path)
    yaml, env = _config(tmp_path, db)
    result = runner.invoke(
        app,
        ["run-execution", "--execute", "--config", str(yaml), "--env", str(env)],
    )
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    conn = sqlite3.connect(str(tmp_path / "cache" / "ls_equity_fund.db"))
    try:
        row = conn.execute(
            """
            SELECT timestamp, ticker, side, shares, limit_price, fill_price,
                   slippage_bps, status, broker_order_id, signal_price,
                   is_closing_trade, run_id, tif, chunk_index, chunk_total
            FROM orders
            """
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[1] == "AAA"
    assert row[4] == 100.0
    assert row[5] == 100.0
    assert row[7] == "FILLED"
    assert row[8]
    assert row[9] == 100.0
    assert row[12] == "DAY"
