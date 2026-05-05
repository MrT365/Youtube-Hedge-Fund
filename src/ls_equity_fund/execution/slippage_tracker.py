"""Side-aware slippage capture and 30-day rolling stats (EXEC-04)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SlippageStats:
    avg_bps: float
    median_bps: float
    p95_bps: float
    total_cost_usd: float
    worst_5: pd.DataFrame


def slippage_bps(*, side: str, signal_price: float, fill_price: float) -> float:
    if signal_price <= 0:
        return 0.0
    side_norm = side.upper()
    raw = (fill_price - signal_price) / signal_price * 10_000.0
    if side_norm in {"SELL", "SELL_SHORT", "SHORT"}:
        return -raw
    return raw


def record_slippage(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ticker: str,
    side: str,
    signal_price: float,
    fill_price: float,
    timestamp: int | None = None,
) -> float:
    bps = slippage_bps(side=side, signal_price=signal_price, fill_price=fill_price)
    with conn:
        conn.execute(
            """
            INSERT INTO slippage_snapshots (
                run_id, ticker, side, fill_bps, signal_price, fill_price, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, ticker, side, bps, signal_price, fill_price, timestamp or int(time.time())),
        )
    return bps


def rolling_stats(conn: sqlite3.Connection, *, now_ts: int | None = None, days: int = 30) -> SlippageStats:
    now = now_ts or int(time.time())
    cutoff = now - days * 86_400
    df = pd.read_sql_query(
        """
        SELECT ticker, side, fill_bps, signal_price, fill_price, timestamp
        FROM slippage_snapshots
        WHERE timestamp >= ?
        """,
        conn,
        params=[cutoff],
    )
    if df.empty:
        return SlippageStats(0.0, 0.0, 0.0, 0.0, df)
    bps = pd.to_numeric(df["fill_bps"], errors="coerce").fillna(0.0)
    notional = pd.to_numeric(df["fill_price"], errors="coerce").fillna(0.0)
    total_cost = float(((bps / 10_000.0) * notional).sum())
    worst = df.assign(fill_bps=bps).sort_values("fill_bps", ascending=False).head(5)
    return SlippageStats(
        avg_bps=float(bps.mean()),
        median_bps=float(bps.median()),
        p95_bps=float(np.percentile(bps.to_numpy(dtype=float), 95)),
        total_cost_usd=total_cost,
        worst_5=worst,
    )


__all__ = ["SlippageStats", "record_slippage", "rolling_stats", "slippage_bps"]
