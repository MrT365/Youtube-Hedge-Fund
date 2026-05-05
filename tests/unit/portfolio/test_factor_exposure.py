"""Factor-exposure calculator tests (PORT-08)."""

from __future__ import annotations

import sqlite3
import time
from datetime import date

import pandas as pd

from ls_equity_fund.portfolio.factor_exposure import BASE_FACTORS, compute_factor_exposure


def _seed_parent(conn: sqlite3.Connection, asof: date, rows: list[tuple[str, str, float]]) -> None:
    now = int(time.time())
    payload = [
        (t, asof.isoformat(), f, score, "Tech", 1, now)
        for (t, f, score) in rows
    ]
    with conn:
        conn.executemany(
            "INSERT INTO factor_scores_parent ("
            "ticker, score_date, factor, parent_score, sector, n_subfactors_used, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            payload,
        )


def test_factor_exposure_long_only(conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 1)
    _seed_parent(conn, asof, [
        ("AAA", "momentum", 90.0),
        ("BBB", "momentum", 80.0),
    ])
    weights = pd.Series({"AAA": 0.04, "BBB": 0.04})
    exp = compute_factor_exposure(conn, weights=weights, asof=asof)
    assert "momentum" in exp.index
    # weight-avg = (0.04*90 + 0.04*80) / 0.08 = 85
    assert abs(exp.loc["momentum", "long_avg"] - 85.0) < 1e-6
    assert pd.isna(exp.loc["momentum", "short_avg"])
    assert pd.isna(exp.loc["momentum", "ls_spread"])


def test_factor_exposure_long_short_spread(conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 1)
    _seed_parent(conn, asof, [
        ("L1", "momentum", 90.0),
        ("L2", "momentum", 80.0),
        ("S1", "momentum", 20.0),
        ("S2", "momentum", 10.0),
    ])
    weights = pd.Series({"L1": 0.05, "L2": 0.05, "S1": -0.05, "S2": -0.05})
    exp = compute_factor_exposure(conn, weights=weights, asof=asof)
    # long_avg = 85, short_avg = 15, spread = 70
    assert abs(exp.loc["momentum", "long_avg"] - 85.0) < 1e-6
    assert abs(exp.loc["momentum", "short_avg"] - 15.0) < 1e-6
    assert abs(exp.loc["momentum", "ls_spread"] - 70.0) < 1e-6
    assert exp.loc["momentum", "n_long"] == 2
    assert exp.loc["momentum", "n_short"] == 2


def test_factor_exposure_empty_when_no_weights(conn: sqlite3.Connection) -> None:
    exp = compute_factor_exposure(conn, weights=pd.Series(dtype=float), asof=date(2026, 5, 1))
    assert exp.empty


def test_factor_exposure_covers_all_base_factors(conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 1)
    rows = [("AAA", f, 50.0) for f in BASE_FACTORS]
    _seed_parent(conn, asof, rows)
    exp = compute_factor_exposure(conn, weights=pd.Series({"AAA": 0.05}), asof=asof)
    for f in BASE_FACTORS:
        assert f in exp.index
