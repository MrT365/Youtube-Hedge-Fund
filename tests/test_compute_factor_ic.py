"""Unit test for ``scripts/compute_factor_ic.py``.

Builds a synthetic 30-ticker by 50-trading-day fixture in a tmp SQLite DB
(real schemas - not :memory: because ``main()`` does ``db_path.exists()``).

Tickers are constructed so:

  * Forward 20-day returns are monotonically increasing in ticker index — a
    random walk with per-ticker drift ``np.linspace(-0.001, +0.001, 30)``
    means top-i tickers compound up, bottom-i drift down. Over a 20-day
    forward window the drift differential dominates the per-step noise so
    the cross-sectional rank of realised returns is stable.
  * ``momentum`` parent_score is monotonically increasing in ticker index
    (noisy linspace 0→100) → Spearman vs forward returns is strongly
    positive ⇒ ``factor_ic_momentum`` clears the 0.03 promotion threshold.
  * ``value`` parent_score is monotonically DECREASING in ticker index
    (100→0) → Spearman vs the same forward returns is strongly negative.
  * The 6 remaining factors get uniform-random scores → IC ≈ 0.

Test must not touch ``cache/ls_equity_fund.db`` and makes no network calls.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import compute_factor_ic  # noqa: E402  — sys.path-injected scripts/ module

N_TICKERS = 30
N_DAYS_WITH_FWD = 50
N_DAYS_TOTAL = N_DAYS_WITH_FWD + compute_factor_ic.FORWARD_DAYS  # 70 trading days
SEED = 42
PARENT_FACTORS = compute_factor_ic.PARENT_FACTORS


def _trading_days(n: int, *, end: date | None = None) -> list[str]:
    """Return ``n`` weekday ISO date strings ending at ``end`` (default 2026-05-01)."""
    end = end or date(2026, 5, 1)
    out: list[date] = []
    cursor = end
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return [d.isoformat() for d in reversed(out)]


def _create_schema(conn: sqlite3.Connection) -> None:
    """Minimal subset of the real DDL — only columns the script reads/writes.

    Real DB has additional columns (sector, computed_at, OHLCV beyond adj_close,
    etc.) but the script only touches the four below.
    """
    with conn:
        conn.execute(
            """
            CREATE TABLE factor_scores_parent (
                ticker        TEXT NOT NULL,
                score_date    TEXT NOT NULL,
                factor        TEXT NOT NULL,
                parent_score  REAL,
                PRIMARY KEY (ticker, score_date, factor)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE daily_prices (
                ticker      TEXT NOT NULL,
                date        TEXT NOT NULL,
                adj_close   REAL,
                PRIMARY KEY (ticker, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE tear_sheet_metrics (
                run_id        TEXT NOT NULL,
                date          TEXT NOT NULL,
                metric_name   TEXT NOT NULL,
                metric_value  REAL NOT NULL,
                PRIMARY KEY (run_id, date, metric_name)
            )
            """
        )


def _seed(conn: sqlite3.Connection) -> None:
    rng = np.random.default_rng(SEED)
    tickers = [f"T{i:02d}" for i in range(N_TICKERS)]
    dates = _trading_days(N_DAYS_TOTAL)
    score_dates = dates[:N_DAYS_WITH_FWD]

    # Random walk with per-ticker drift; drifts monotonic in i.
    drifts = np.linspace(-0.001, +0.001, N_TICKERS)
    rets = rng.normal(loc=drifts[:, None], scale=0.005, size=(N_TICKERS, N_DAYS_TOTAL))
    prices = 100.0 * np.cumprod(1.0 + rets, axis=1)

    price_rows = [
        (tickers[i], dates[d], float(prices[i, d]))
        for i in range(N_TICKERS)
        for d in range(N_DAYS_TOTAL)
    ]

    # Factor scores. Momentum monotonic ↑, value monotonic ↓, others uniform noise.
    monotonic = np.linspace(0.0, 100.0, N_TICKERS)
    momentum = monotonic[:, None] + rng.normal(0.0, 10.0, size=(N_TICKERS, N_DAYS_WITH_FWD))
    value = (100.0 - monotonic)[:, None] + rng.normal(
        0.0, 10.0, size=(N_TICKERS, N_DAYS_WITH_FWD)
    )

    score_rows: list[tuple[str, str, str, float]] = []
    for i, t in enumerate(tickers):
        for k, sd in enumerate(score_dates):
            score_rows.append((t, sd, "momentum", float(momentum[i, k])))
            score_rows.append((t, sd, "value", float(value[i, k])))

    other_factors = [f for f in PARENT_FACTORS if f not in ("momentum", "value")]
    for factor in other_factors:
        noise = rng.uniform(0.0, 100.0, size=(N_TICKERS, N_DAYS_WITH_FWD))
        for i, t in enumerate(tickers):
            for k, sd in enumerate(score_dates):
                score_rows.append((t, sd, factor, float(noise[i, k])))

    with conn:
        conn.executemany(
            "INSERT INTO daily_prices (ticker, date, adj_close) VALUES (?, ?, ?)",
            price_rows,
        )
        conn.executemany(
            "INSERT INTO factor_scores_parent "
            "(ticker, score_date, factor, parent_score) VALUES (?, ?, ?, ?)",
            score_rows,
        )


@pytest.fixture
def synthetic_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ic_test.db"
    conn = sqlite3.connect(str(db_path))
    try:
        _create_schema(conn)
        _seed(conn)
    finally:
        conn.close()
    return db_path


def _read_factor_ic(db_path: Path) -> dict[str, float]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT metric_name, metric_value FROM tear_sheet_metrics "
            "WHERE metric_name LIKE 'factor_ic_%'"
        )
        return {name: float(value) for name, value in cur.fetchall()}
    finally:
        conn.close()


def _count_factor_ic_rows(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM tear_sheet_metrics WHERE metric_name LIKE 'factor_ic_%'"
            ).fetchone()[0]
        )
    finally:
        conn.close()


class TestComputeFactorIC:
    """Cover the IC formula on a deterministic synthetic fixture."""

    def test_signs_threshold_and_coverage(self, synthetic_db: Path) -> None:
        rc = compute_factor_ic.main(db_path=synthetic_db)
        assert rc == 0
        metrics = _read_factor_ic(synthetic_db)

        # All 8 parent factors persisted.
        expected = {f"factor_ic_{f}" for f in PARENT_FACTORS}
        assert expected <= set(metrics)
        assert len(metrics) == len(PARENT_FACTORS)

        # Constructed-positive momentum should clear the 0.03 promotion threshold.
        assert metrics["factor_ic_momentum"] > compute_factor_ic.PASS_THRESHOLD, (
            f"momentum IC {metrics['factor_ic_momentum']:.4f} should exceed "
            f"{compute_factor_ic.PASS_THRESHOLD}"
        )

        # Constructed-negative value should land below zero.
        assert metrics["factor_ic_value"] < 0.0, (
            f"value IC {metrics['factor_ic_value']:.4f} should be negative"
        )

        # Sanity: every IC is bounded by [-1, 1] (Spearman is a correlation).
        for name, ic in metrics.items():
            assert -1.0 <= ic <= 1.0, f"{name} IC {ic} outside [-1, 1]"

    def test_idempotent_no_row_inflation(self, synthetic_db: Path) -> None:
        compute_factor_ic.main(db_path=synthetic_db)
        first = _read_factor_ic(synthetic_db)
        first_count = _count_factor_ic_rows(synthetic_db)

        compute_factor_ic.main(db_path=synthetic_db)
        second = _read_factor_ic(synthetic_db)
        second_count = _count_factor_ic_rows(synthetic_db)

        # DELETE-then-INSERT must replace, not append. Count stays at exactly 8.
        assert first_count == len(PARENT_FACTORS)
        assert second_count == len(PARENT_FACTORS)
        # Same fixture ⇒ identical results across runs.
        assert first == second
