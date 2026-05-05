"""SCORE-10 idempotent persistence for Phase 2 factor scores."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

_FACTOR_SCORES_COLS = (
    "ticker",
    "score_date",
    "factor",
    "sub_factor",
    "raw_value",
    "percentile_rank",
    "sector",
    "n_in_sector",
    "sufficient_history",
    "computed_at",
)
_PARENT_COLS = (
    "ticker",
    "score_date",
    "factor",
    "parent_score",
    "sector",
    "n_subfactors_used",
    "computed_at",
)


def _rows_with_nulls(df: pd.DataFrame) -> list[tuple[object, ...]]:
    return [
        tuple(None if pd.isna(value) else value for value in row)
        for row in df.itertuples(index=False, name=None)
    ]


def write_factor_scores(conn: sqlite3.Connection, rows: pd.DataFrame) -> int:
    """Insert or replace long-format sub-factor score rows."""
    if rows.empty:
        return 0
    df = rows.copy()
    if "computed_at" not in df.columns:
        df["computed_at"] = int(time.time())
    if "sufficient_history" not in df.columns:
        df["sufficient_history"] = 1
    df = df[list(_FACTOR_SCORES_COLS)]
    payload = _rows_with_nulls(df)
    sql = (
        f"INSERT OR REPLACE INTO factor_scores ({', '.join(_FACTOR_SCORES_COLS)}) "
        f"VALUES ({', '.join('?' * len(_FACTOR_SCORES_COLS))})"
    )
    with conn:
        conn.executemany(sql, payload)
    log.info("factor_scores_written", rows=len(payload))
    return len(payload)


def write_parent_scores(conn: sqlite3.Connection, rows: pd.DataFrame) -> int:
    """Insert or replace parent factor score rows."""
    if rows.empty:
        return 0
    df = rows.copy()
    if "computed_at" not in df.columns:
        df["computed_at"] = int(time.time())
    df = df[list(_PARENT_COLS)]
    payload = _rows_with_nulls(df)
    sql = (
        f"INSERT OR REPLACE INTO factor_scores_parent ({', '.join(_PARENT_COLS)}) "
        f"VALUES ({', '.join('?' * len(_PARENT_COLS))})"
    )
    with conn:
        conn.executemany(sql, payload)
    log.info("factor_scores_parent_written", rows=len(payload))
    return len(payload)


__all__ = ["write_factor_scores", "write_parent_scores"]
