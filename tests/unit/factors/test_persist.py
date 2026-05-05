"""Persistence tests for factor score writers."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from ls_equity_fund.factors.persist import write_factor_scores, write_parent_scores


def _score_row(raw_value: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["A"],
            "score_date": ["2026-05-04"],
            "factor": ["momentum"],
            "sub_factor": ["mom_6m"],
            "raw_value": [raw_value],
            "percentile_rank": [75.0],
            "sector": ["Tech"],
            "n_in_sector": [10],
            "sufficient_history": [1],
        }
    )


def test_write_factor_scores_idempotent_rerun(migrated_conn: sqlite3.Connection) -> None:
    assert write_factor_scores(migrated_conn, _score_row(1.0)) == 1
    assert write_factor_scores(migrated_conn, _score_row(2.0)) == 1
    rows = migrated_conn.execute("SELECT raw_value FROM factor_scores").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 2.0


def test_write_factor_scores_long_format_dataframe(migrated_conn: sqlite3.Connection) -> None:
    rows = pd.concat([_score_row(1.0), _score_row(2.0).assign(ticker="B")], ignore_index=True)
    assert write_factor_scores(migrated_conn, rows) == 2
    assert migrated_conn.execute("SELECT COUNT(*) FROM factor_scores").fetchone()[0] == 2


def test_write_parent_scores_idempotent(migrated_conn: sqlite3.Connection) -> None:
    rows = pd.DataFrame(
        {
            "ticker": ["A"],
            "score_date": ["2026-05-04"],
            "factor": ["momentum"],
            "parent_score": [50.0],
            "sector": ["Tech"],
            "n_subfactors_used": [6],
        }
    )
    assert write_parent_scores(migrated_conn, rows) == 1
    assert write_parent_scores(migrated_conn, rows.assign(parent_score=80.0)) == 1
    result = migrated_conn.execute("SELECT COUNT(*), parent_score FROM factor_scores_parent").fetchone()
    assert result == (1, 80.0)


def test_write_factor_scores_handles_nan_to_null(migrated_conn: sqlite3.Connection) -> None:
    rows = _score_row(np.nan).assign(percentile_rank=np.nan)
    write_factor_scores(migrated_conn, rows)
    result = migrated_conn.execute(
        "SELECT raw_value, percentile_rank FROM factor_scores WHERE ticker = 'A'"
    ).fetchone()
    assert result == (None, None)
