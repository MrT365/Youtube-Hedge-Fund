"""End-to-end ``meridian run-portfolio`` CLI tests (Phase 5 SC1+SC2+SC3+SC4)."""

from __future__ import annotations

import shutil
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from typer.testing import CliRunner

from ls_equity_fund.cli.app import app

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _alembic_cfg(db_path: Path) -> AlembicConfig:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _seeded_db(tmp_path: Path, asof: date) -> Path:
    """Build a tmp DB with a 12-name universe, factor_scores (combined), and
    enough OHLCV history for a 60-day beta calc + 20-day ADV."""
    db_path = tmp_path / "phase5.db"
    alembic_command.upgrade(_alembic_cfg(db_path), "head")
    conn = sqlite3.connect(str(db_path))
    try:
        sectors = ["Tech", "Financials", "Health", "Consumer"]
        tickers = [f"M{i:02d}" for i in range(12)]
        now = int(time.time())
        with conn:
            for i, t in enumerate(tickers):
                conn.execute(
                    "INSERT INTO universe (ticker, company_name, sector, "
                    "first_seen_date, inclusion_window, last_updated) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (t, t, sectors[i % len(sectors)], "2020-01-01", "[2020-01-01,)", now),
                )
            # SPY for beta
            conn.execute(
                "INSERT INTO universe (ticker, company_name, sector, "
                "first_seen_date, inclusion_window, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("SPY", "S&P 500 ETF", "ETF", "2020-01-01", "[2020-01-01,)", now),
            )

        # Generate 100 days of synthetic prices.
        rng = np.random.default_rng(7)
        days = []
        d = asof
        while len(days) < 100:
            if d.weekday() < 5:
                days.append(d)
            d -= timedelta(days=1)
        days = list(reversed(days))

        spy_rets = rng.normal(0.0005, 0.01, size=len(days))
        spy_prices = 400 * np.cumprod(1 + spy_rets)
        rows: list[tuple] = []
        for d_, p in zip(days, spy_prices, strict=False):
            rows.append(
                (
                    "SPY",
                    d_.isoformat(),
                    float(p),
                    float(p),
                    float(p),
                    float(p),
                    float(p),
                    1_000_000_000,
                )
            )

        for i, t in enumerate(tickers):
            beta_load = 0.5 + (i / len(tickers))  # 0.5 → 1.5
            stock_rets = beta_load * spy_rets + rng.normal(0, 0.005, size=len(days))
            prices = 50 * np.cumprod(1 + stock_rets)
            for d_, p in zip(days, prices, strict=False):
                rows.append(
                    (t, d_.isoformat(), float(p), float(p), float(p), float(p), float(p), 5_000_000)
                )
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_prices "
                "(ticker, date, open, high, low, close, adj_close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

        # Combined factor scores: 100, 92, 84, ..., monotonic so longs/shorts split cleanly.
        score_rows = []
        for i, t in enumerate(tickers):
            score = 100.0 - i * 8.0
            score_rows.append(
                (
                    t,
                    asof.isoformat(),
                    "combined",
                    "combined",
                    0.0,
                    float(score),
                    sectors[i % len(sectors)],
                    1,
                    1,
                    now,
                )
            )
        with conn:
            conn.executemany(
                "INSERT INTO factor_scores ("
                "ticker, score_date, factor, sub_factor, raw_value, percentile_rank, "
                "sector, n_in_sector, sufficient_history, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                score_rows,
            )
    finally:
        conn.close()
    return db_path


def _write_config_yaml(yaml_dest: Path, db_path: Path, cache_dir: Path) -> Path:
    src = REPO_ROOT / "config.yaml.example"
    text = src.read_text(encoding="utf-8")
    text = text.replace("cache_dir: cache", f"cache_dir: {cache_dir}")
    yaml_dest.write_text(text, encoding="utf-8")
    # Move the seeded DB to where the config expects it: <cache_dir>/ls_equity_fund.db
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "ls_equity_fund.db"
    shutil.copy(db_path, target)
    return yaml_dest


def _write_env(env_dest: Path) -> Path:
    env_dest.write_text(
        "ANTHROPIC_API_KEY=sk-ant-test-key\n"
        "IBKR_USERNAME=test\n"
        "IBKR_PASSWORD=test\n"
        "SEC_USER_AGENT=Meridian test\n",
        encoding="utf-8",
    )
    return env_dest


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_portfolio_conviction_whatif_end_to_end(tmp_path: Path) -> None:
    asof = date(2026, 5, 1)
    seeded = _seeded_db(tmp_path, asof)
    cache_dir = tmp_path / "cache"
    yaml = _write_config_yaml(tmp_path / "config.yaml", seeded, cache_dir)
    env = _write_env(tmp_path / ".env")

    result = runner.invoke(
        app,
        [
            "run-portfolio",
            "--whatif",
            "--optimize-method",
            "conviction",
            "--asof",
            asof.isoformat(),
            "--config",
            str(yaml),
            "--env",
            str(env),
        ],
    )
    assert result.exit_code == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    out = result.stdout
    # SC1 — book exists
    assert "Target book" in out
    assert "longs=" in out and "shorts=" in out
    # SC1 — gross + net exposure printed
    assert "gross=" in out and "net=" in out
    # SC2 — rebalance output with cost decomposition
    assert "Rebalance" in out
    assert "commission_usd" in out or "open" in out  # at least one trade
    # SC4 — schedule advisory section
    assert "Schedule advisories" in out

    # SC3 — position_approvals + portfolio_history populated
    conn = sqlite3.connect(str(cache_dir / "ls_equity_fund.db"))
    try:
        n_approvals = conn.execute("SELECT COUNT(*) FROM position_approvals").fetchone()[0]
        n_history = conn.execute("SELECT COUNT(*) FROM portfolio_history").fetchone()[0]
    finally:
        conn.close()
    assert n_approvals > 0, "expected position_approvals rows after --whatif"
    assert n_history > 0, "expected portfolio_history rows after --whatif"


def test_run_portfolio_mvo_raises_phase7_message(tmp_path: Path) -> None:
    asof = date(2026, 5, 1)
    seeded = _seeded_db(tmp_path, asof)
    cache_dir = tmp_path / "cache"
    yaml = _write_config_yaml(tmp_path / "config.yaml", seeded, cache_dir)
    env = _write_env(tmp_path / ".env")

    result = runner.invoke(
        app,
        [
            "run-portfolio",
            "--whatif",
            "--optimize-method",
            "mvo",
            "--asof",
            asof.isoformat(),
            "--config",
            str(yaml),
            "--env",
            str(env),
        ],
    )
    assert result.exit_code == 8
    assert "MVO coming in Phase 7" in result.stderr or "Phase 7" in result.stderr


def test_run_portfolio_invalid_method_exits_5(tmp_path: Path) -> None:
    asof = date(2026, 5, 1)
    seeded = _seeded_db(tmp_path, asof)
    cache_dir = tmp_path / "cache"
    yaml = _write_config_yaml(tmp_path / "config.yaml", seeded, cache_dir)
    env = _write_env(tmp_path / ".env")
    result = runner.invoke(
        app,
        [
            "run-portfolio",
            "--whatif",
            "--optimize-method",
            "bogus",
            "--asof",
            asof.isoformat(),
            "--config",
            str(yaml),
            "--env",
            str(env),
        ],
    )
    assert result.exit_code == 5


def test_run_portfolio_no_scores_exits_6(tmp_path: Path) -> None:
    asof = date(2026, 5, 1)
    seeded = _seeded_db(tmp_path, asof)
    # Wipe combined factor scores to simulate no L2 data.
    conn = sqlite3.connect(str(seeded))
    with conn:
        conn.execute("DELETE FROM factor_scores")
    conn.close()
    cache_dir = tmp_path / "cache"
    yaml = _write_config_yaml(tmp_path / "config.yaml", seeded, cache_dir)
    env = _write_env(tmp_path / ".env")

    result = runner.invoke(
        app,
        [
            "run-portfolio",
            "--whatif",
            "--asof",
            asof.isoformat(),
            "--config",
            str(yaml),
            "--env",
            str(env),
        ],
    )
    assert result.exit_code == 6
