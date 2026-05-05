"""SCORE-08 institutional-flow factor.

PIT rule: the latest 13F period is the maximum ``period_end`` whose filing was
known by ``asof``. This preserves the 13F filing lag during historical replay.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors._pit import universe_tickers
from ls_equity_fund.factors.composer import register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = (
    "inst_fund_count",
    "inst_net_change",
    "inst_multi_fund_open_flag",
)
NEW_POSITION_WINDOW_DAYS = 90
MULTI_FUND_OPEN_THRESHOLD = 3


@register_factor("institutional")
def compute_institutional(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Return long-format institutional sub-factor raw values."""
    universe = universe_tickers(conn, tickers)
    if not universe:
        return _empty_result()

    rows: list[dict[str, object]] = []
    for ticker in universe:
        period_end = _latest_period_end_pit(conn, ticker, asof)
        if period_end is None:
            values = {
                "inst_fund_count": np.nan,
                "inst_net_change": np.nan,
                "inst_multi_fund_open_flag": np.nan,
            }
        else:
            values = {
                "inst_fund_count": float(_fund_count_at_period(conn, ticker, period_end, asof)),
                "inst_net_change": _net_change_at_period(conn, ticker, period_end, asof),
                "inst_multi_fund_open_flag": float(_multi_fund_open_flag(conn, ticker, asof)),
            }

        rows.extend(
            {"ticker": ticker, "sub_factor": sub_factor, "raw_value": values[sub_factor]}
            for sub_factor in SUB_FACTORS
        )

    out = pd.DataFrame(rows, columns=["ticker", "sub_factor", "raw_value"])
    out["raw_value"] = out["raw_value"].astype("float64")
    log.info("compute_institutional_complete", n_tickers=len(universe), n_rows=len(out))
    return out


def _latest_period_end_pit(conn: sqlite3.Connection, ticker: str, asof: date) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(period_end)
        FROM institutional_holdings
        WHERE ticker = ? AND filed_date <= ?
        """,
        (ticker, asof.isoformat()),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else None


def _fund_count_at_period(
    conn: sqlite3.Connection,
    ticker: str,
    period_end: str,
    asof: date,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT cik)
        FROM institutional_holdings
        WHERE ticker = ? AND period_end = ? AND filed_date <= ?
        """,
        (ticker, period_end, asof.isoformat()),
    ).fetchone()
    return int(row[0] or 0)


def _net_change_at_period(
    conn: sqlite3.Connection,
    ticker: str,
    period_end: str,
    asof: date,
) -> float:
    rows = conn.execute(
        """
        SELECT change_shares, value_usd, shares
        FROM institutional_holdings
        WHERE ticker = ? AND period_end = ? AND filed_date <= ?
        """,
        (ticker, period_end, asof.isoformat()),
    ).fetchall()
    total = 0.0
    counted = 0
    for change_shares, value_usd, shares in rows:
        if change_shares is None or value_usd is None or shares in (None, 0):
            continue
        value_per_share = float(value_usd) / float(shares)
        total += float(change_shares) * value_per_share
        counted += 1
    return total if counted else float("nan")


def _multi_fund_open_flag(conn: sqlite3.Connection, ticker: str, asof: date) -> int:
    start = (asof - timedelta(days=NEW_POSITION_WINDOW_DAYS)).isoformat()
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT cik)
        FROM institutional_holdings
        WHERE ticker = ?
          AND is_new_position = 1
          AND filed_date BETWEEN ? AND ?
        """,
        (ticker, start, asof.isoformat()),
    ).fetchone()
    return 1 if int(row[0] or 0) >= MULTI_FUND_OPEN_THRESHOLD else 0


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_institutional"]
