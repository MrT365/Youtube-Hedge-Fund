"""Portfolio state persistence helpers (PORT-06).

Three tables managed here:
  * portfolio_positions  — current book of record
  * portfolio_history    — append-only daily snapshot (one row per
                            (asof_date, ticker), plus a __PORTFOLIO__ aggregate row)
  * position_approvals   — per-rebalance audit trail of every proposed trade

All writes use ``conn`` in a ``with conn:`` block so the implicit transaction
commits on success and rolls back on exception.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

import pandas as pd

PORTFOLIO_AGGREGATE_TICKER = "__PORTFOLIO__"


@dataclass(frozen=True)
class CurrentPosition:
    """Row-of-record from portfolio_positions."""

    ticker: str
    side: str  # 'long' | 'short'
    shares: float
    entry_price: float
    entry_date: str
    current_price: float | None
    sector: str | None


def load_current_positions(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return current book — empty DataFrame when no positions exist yet."""
    df = pd.read_sql_query(
        """
        SELECT ticker, side, shares, entry_price, entry_date, current_price,
               unrealized_pnl, sector, factor_scores_at_entry, beta_at_entry,
               last_marked_at
        FROM portfolio_positions
        ORDER BY ticker
        """,
        conn,
    )
    return df


def upsert_position(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    side: str,
    shares: float,
    entry_price: float,
    entry_date: date_type,
    current_price: float | None,
    sector: str | None,
    factor_scores_at_entry: dict[str, float] | None,
    beta_at_entry: float | None,
) -> None:
    """Insert or update a position. ``shares`` should be signed (negative for
    shorts)."""
    payload = json.dumps(factor_scores_at_entry or {}, sort_keys=True)
    unrealized_pnl = (
        (current_price - entry_price) * shares
        if current_price is not None and entry_price is not None
        else None
    )
    with conn:
        conn.execute(
            """
            INSERT INTO portfolio_positions (
                ticker, side, shares, entry_price, entry_date,
                current_price, unrealized_pnl, sector,
                factor_scores_at_entry, beta_at_entry, last_marked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, side) DO UPDATE SET
                shares = excluded.shares,
                entry_price = excluded.entry_price,
                entry_date = excluded.entry_date,
                current_price = excluded.current_price,
                unrealized_pnl = excluded.unrealized_pnl,
                sector = excluded.sector,
                factor_scores_at_entry = excluded.factor_scores_at_entry,
                beta_at_entry = excluded.beta_at_entry,
                last_marked_at = excluded.last_marked_at
            """,
            (
                ticker,
                side,
                shares,
                entry_price,
                entry_date.isoformat(),
                current_price,
                unrealized_pnl,
                sector,
                payload,
                beta_at_entry,
                int(time.time()),
            ),
        )


def close_position(conn: sqlite3.Connection, *, ticker: str, side: str) -> None:
    """Remove a row from the book of record (after a full close)."""
    with conn:
        conn.execute(
            "DELETE FROM portfolio_positions WHERE ticker = ? AND side = ?",
            (ticker, side),
        )


def write_position_approvals(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    asof: date_type,
    rows: Iterable[Mapping[str, Any]],
    optimizer: str,
) -> int:
    """Persist a batch of position_approvals rows. Returns row count."""
    payload: list[tuple[object, ...]] = []
    decided_at = int(time.time())
    for r in rows:
        payload.append(
            (
                run_id,
                asof.isoformat(),
                r["ticker"],
                r["side"],
                optimizer,
                r.get("tilt_bucket"),
                float(r.get("base_weight", 0.0)),
                float(r.get("tilted_weight", 0.0)),
                float(r.get("adv_capped_weight", 0.0)),
                int(bool(r.get("earnings_halved", False))),
                float(r.get("beta_adjusted_weight", 0.0)),
                float(r.get("final_weight", 0.0)),
                float(r.get("final_shares", 0.0)),
                float(r.get("target_dollar", 0.0)),
                r.get("limit_price"),
                r.get("score"),
                r.get("sector"),
                r.get("beta"),
                json.dumps(r.get("advisory_flags") or []),
                decided_at,
            )
        )
    if not payload:
        return 0
    with conn:
        conn.executemany(
            """
            INSERT INTO position_approvals (
                run_id, asof_date, ticker, side, optimizer,
                tilt_bucket, base_weight, tilted_weight,
                adv_capped_weight, earnings_halved, beta_adjusted_weight,
                final_weight, final_shares, target_dollar, limit_price,
                score, sector, beta, advisory_flags, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, ticker, side) DO UPDATE SET
                tilt_bucket = excluded.tilt_bucket,
                base_weight = excluded.base_weight,
                tilted_weight = excluded.tilted_weight,
                adv_capped_weight = excluded.adv_capped_weight,
                earnings_halved = excluded.earnings_halved,
                beta_adjusted_weight = excluded.beta_adjusted_weight,
                final_weight = excluded.final_weight,
                final_shares = excluded.final_shares,
                target_dollar = excluded.target_dollar,
                limit_price = excluded.limit_price,
                score = excluded.score,
                sector = excluded.sector,
                beta = excluded.beta,
                advisory_flags = excluded.advisory_flags,
                decided_at = excluded.decided_at
            """,
            payload,
        )
    return len(payload)


def write_portfolio_history(
    conn: sqlite3.Connection,
    *,
    asof: date_type,
    per_position_rows: Iterable[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
) -> int:
    """Append the daily portfolio_history snapshot."""
    recorded_at = int(time.time())
    rows: list[tuple[object, ...]] = []
    for r in per_position_rows:
        rows.append(
            (
                asof.isoformat(),
                r["ticker"],
                r.get("side"),
                float(r.get("shares", 0.0)),
                r.get("mark_price"),
                float(r.get("market_value", 0.0)),
                float(r.get("weight", 0.0)),
                r.get("unrealized_pnl"),
                r.get("beta"),
                r.get("sector"),
                None,
                None,
                None,
                None,
                None,
                recorded_at,
            )
        )
    rows.append(
        (
            asof.isoformat(),
            PORTFOLIO_AGGREGATE_TICKER,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            float(aggregate.get("gross_exposure", 0.0)),
            float(aggregate.get("net_exposure", 0.0)),
            float(aggregate.get("net_beta", 0.0)),
            float(aggregate.get("long_book_beta", 0.0)),
            float(aggregate.get("short_book_beta", 0.0)),
            recorded_at,
        )
    )
    with conn:
        conn.executemany(
            """
            INSERT INTO portfolio_history (
                asof_date, ticker, side, shares, mark_price, market_value,
                weight, unrealized_pnl, beta, sector,
                gross_exposure, net_exposure, net_beta,
                long_book_beta, short_book_beta, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asof_date, ticker) DO UPDATE SET
                side = excluded.side,
                shares = excluded.shares,
                mark_price = excluded.mark_price,
                market_value = excluded.market_value,
                weight = excluded.weight,
                unrealized_pnl = excluded.unrealized_pnl,
                beta = excluded.beta,
                sector = excluded.sector,
                gross_exposure = excluded.gross_exposure,
                net_exposure = excluded.net_exposure,
                net_beta = excluded.net_beta,
                long_book_beta = excluded.long_book_beta,
                short_book_beta = excluded.short_book_beta,
                recorded_at = excluded.recorded_at
            """,
            rows,
        )
    return len(rows)


__all__ = [
    "PORTFOLIO_AGGREGATE_TICKER",
    "CurrentPosition",
    "close_position",
    "load_current_positions",
    "upsert_position",
    "write_portfolio_history",
    "write_position_approvals",
]
