#!/usr/bin/env python3
"""Nightly factor-IC computation (BACKTEST-02 lite — promotion-ceremony feed).

For each of the 8 parent factors (momentum, value, quality, growth, revisions,
short_interest, insider, institutional) compute the cross-sectional Spearman
rank correlation between ``parent_score`` on date T and the ticker's forward
20 trading-day return, then average across all dates where T+20 prices exist.
This is the canonical Information Coefficient (IC).

Persists one row per factor to ``tear_sheet_metrics`` as
``metric_name = factor_ic_<factor>``. ``promote_to_live.py`` criterion 4 reads
``COUNT(*) WHERE metric_name LIKE 'factor_ic_%' AND metric_value > 0.03`` and
requires ≥ 4 to pass. Without this script's output that count is 0 and
promotion is permanently blocked.

Run nightly after ``run-scoring`` so that day's parent_scores are included.
Initial run is a no-op until ≥ 20 trading days of OHLCV exist.

Idempotent: every run DELETEs prior ``factor_ic_*`` rows before INSERT so the
promotion query never accumulates stale values.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "cache" / "ls_equity_fund.db"

PARENT_FACTORS: tuple[str, ...] = (
    "momentum",
    "value",
    "quality",
    "growth",
    "revisions",
    "short_interest",
    "insider",
    "institutional",
)

FORWARD_DAYS = 20  # trading days
PASS_THRESHOLD = 0.03
MIN_TICKERS_PER_DATE = 5  # below this, daily Spearman is too noisy to keep
RUN_ID = "factor_ic"


def _load_factor_scores(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT ticker, score_date, factor, parent_score
        FROM factor_scores_parent
        WHERE factor IN ({placeholders})
          AND parent_score IS NOT NULL
        """.format(placeholders=",".join("?" * len(PARENT_FACTORS))),
        conn,
        params=list(PARENT_FACTORS),
    )
    df["parent_score"] = pd.to_numeric(df["parent_score"], errors="coerce")
    return df.dropna(subset=["parent_score"])


def _load_forward_returns(conn: sqlite3.Connection) -> pd.DataFrame:
    """Build ``(ticker, score_date, fwd_ret)`` over all (ticker, date_T) where
    a T+20 close exists. fwd_ret is the simple percentage return.
    """
    px = pd.read_sql_query(
        "SELECT ticker, date, adj_close FROM daily_prices",
        conn,
    )
    if px.empty:
        return px.assign(fwd_ret=pd.Series(dtype=float)).rename(columns={"date": "score_date"})
    px["adj_close"] = pd.to_numeric(px["adj_close"], errors="coerce")
    px = px.dropna(subset=["adj_close"]).sort_values(["ticker", "date"])
    px["fwd_close"] = px.groupby("ticker")["adj_close"].shift(-FORWARD_DAYS)
    px["fwd_ret"] = px["fwd_close"] / px["adj_close"] - 1.0
    fwd = px.dropna(subset=["fwd_ret"])[["ticker", "date", "fwd_ret"]]
    return fwd.rename(columns={"date": "score_date"})


def compute_factor_ic(scores: pd.DataFrame, fwd: pd.DataFrame, factor: str) -> float:
    """Mean of per-date cross-sectional Spearman correlations.

    Returns 0.0 when no usable date has ≥ ``MIN_TICKERS_PER_DATE`` overlapping
    (score, forward-return) pairs. Caller treats 0.0 as "no signal yet".
    """
    sub = scores[scores["factor"] == factor]
    if sub.empty or fwd.empty:
        return 0.0
    merged = sub.merge(fwd, on=["ticker", "score_date"], how="inner")
    if merged.empty:
        return 0.0
    daily_ics: list[float] = []
    for _, group in merged.groupby("score_date"):
        if len(group) < MIN_TICKERS_PER_DATE:
            continue
        ic = group["parent_score"].corr(group["fwd_ret"], method="spearman")
        if pd.notna(ic):
            daily_ics.append(float(ic))
    if not daily_ics:
        return 0.0
    return float(pd.Series(daily_ics).mean())


def upsert_metrics(conn: sqlite3.Connection, *, asof_date: str, metrics: dict[str, float]) -> None:
    """Idempotent replace: delete any prior factor_ic_* rows then insert fresh."""
    rows = [(RUN_ID, asof_date, name, value) for name, value in metrics.items()]
    with conn:
        conn.execute("DELETE FROM tear_sheet_metrics WHERE metric_name LIKE 'factor_ic_%'")
        conn.executemany(
            "INSERT OR REPLACE INTO tear_sheet_metrics "
            "(run_id, date, metric_name, metric_value) VALUES (?, ?, ?, ?)",
            rows,
        )


def main(db_path: Path | str = DB_PATH) -> int:
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"ERROR: db not found at {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(db_path))
    try:
        scores = _load_factor_scores(conn)
        fwd = _load_forward_returns(conn)
        if scores.empty or fwd.empty:
            print(
                "factor_ic: insufficient data — need factor_scores_parent + "
                f"≥ {FORWARD_DAYS} days of daily_prices. Skipping."
            )
            return 0
        metrics: dict[str, float] = {}
        for factor in PARENT_FACTORS:
            ic = compute_factor_ic(scores, fwd, factor)
            metric_name = f"factor_ic_{factor}"
            metrics[metric_name] = ic
            verdict = "PASS" if ic > PASS_THRESHOLD else "FAIL"
            print(f"{metric_name}: {ic:+.4f} ({verdict} >{PASS_THRESHOLD})")
        asof_date = datetime.utcnow().date().isoformat()
        upsert_metrics(conn, asof_date=asof_date, metrics=metrics)
        n_pass = sum(1 for v in metrics.values() if v > PASS_THRESHOLD)
        print(f"\n{n_pass}/{len(PARENT_FACTORS)} factors PASS — promotion criterion 4 needs ≥ 4")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
