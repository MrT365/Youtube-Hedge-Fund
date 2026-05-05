"""Daily P&L attribution (REPORT-01)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


def decompose_daily_return(
    *,
    daily_return: float,
    net_beta: float,
    spy_return: float,
    sector_return: float = 0.0,
    factor_return: float = 0.0,
) -> dict[str, float]:
    beta_return = net_beta * spy_return
    alpha_return = daily_return - beta_return - sector_return - factor_return
    return {
        "daily_return": daily_return,
        "beta_return": beta_return,
        "sector_return": sector_return,
        "factor_return": factor_return,
        "alpha_return": alpha_return,
        "net_beta": net_beta,
        "spy_return": spy_return,
    }


def compute_daily_attribution(rows: pd.DataFrame, *, run_id: str) -> pd.DataFrame:
    out = []
    for row in rows.itertuples(index=False):
        parts: dict[str, Any] = decompose_daily_return(
            daily_return=float(row.daily_return),
            net_beta=float(row.net_beta),
            spy_return=float(row.spy_return),
            sector_return=float(getattr(row, "sector_return", 0.0)),
            factor_return=float(getattr(row, "factor_return", 0.0)),
        )
        parts["run_id"] = run_id
        parts["date"] = str(row.date)
        out.append(parts)
    return pd.DataFrame(out)


def persist_daily_attribution(
    conn: sqlite3.Connection,
    attribution: pd.DataFrame,
    *,
    output_dir: Path = Path("output"),
) -> Path:
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_attribution (
                run_id, date, daily_return, beta_return, sector_return,
                factor_return, alpha_return, net_beta, spy_return
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.run_id,
                    row.date,
                    row.daily_return,
                    row.beta_return,
                    row.sector_return,
                    row.factor_return,
                    row.alpha_return,
                    row.net_beta,
                    row.spy_return,
                )
                for row in attribution.itertuples(index=False)
            ],
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "daily_attribution.csv"
    attribution.to_csv(path, index=False)
    return path


__all__ = ["compute_daily_attribution", "decompose_daily_return", "persist_daily_attribution"]
