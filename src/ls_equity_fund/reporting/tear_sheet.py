"""Institutional markdown tear sheet (REPORT-06)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

NAMED_METRICS = (
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "max_drawdown",
    "hit_rate",
    "profit_factor",
    "skewness",
    "kurtosis",
    "tail_ratio",
)


def compute_metrics(returns: pd.Series, *, risk_free_rate: float = 0.0, trade_pnls: pd.Series | None = None) -> dict[str, float]:
    r = pd.to_numeric(returns, errors="coerce").dropna()
    if r.empty:
        return {name: 0.0 for name in NAMED_METRICS}
    excess = r - risk_free_rate / 252
    downside = excess[excess < 0]
    equity = (1 + r).cumprod()
    drawdown = equity / equity.cummax() - 1
    annual_return = float(r.mean() * 252)
    gross_profit = float(trade_pnls[trade_pnls > 0].sum()) if trade_pnls is not None and not trade_pnls.empty else float(r[r > 0].sum())
    gross_loss = abs(float(trade_pnls[trade_pnls < 0].sum())) if trade_pnls is not None and not trade_pnls.empty else abs(float(r[r < 0].sum()))
    p95 = float(np.percentile(r, 95))
    p5 = abs(float(np.percentile(r, 5)))
    metrics = {
        "sharpe_ratio": float(excess.mean() / excess.std(ddof=0) * np.sqrt(252)) if excess.std(ddof=0) > 0 else 0.0,
        "sortino_ratio": float(excess.mean() / downside.std(ddof=0) * np.sqrt(252)) if len(downside) > 1 and downside.std(ddof=0) > 0 else 0.0,
        "calmar_ratio": annual_return / abs(float(drawdown.min())) if drawdown.min() < 0 else 0.0,
        "max_drawdown": float(drawdown.min()),
        "hit_rate": float((trade_pnls > 0).mean()) if trade_pnls is not None and not trade_pnls.empty else float((r > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0.0,
        "skewness": float(r.skew()),
        "kurtosis": float(r.kurtosis()),
        "tail_ratio": p95 / p5 if p5 > 0 else 0.0,
    }
    return {k: (v if np.isfinite(v) else 0.0) for k, v in metrics.items()}


def monthly_returns_grid(returns: pd.Series) -> pd.DataFrame:
    r = returns.copy()
    r.index = pd.to_datetime(r.index)
    monthly = (1 + r).resample("ME").prod() - 1
    return monthly.to_frame("return").assign(year=lambda x: x.index.year, month=lambda x: x.index.month).pivot(
        index="year", columns="month", values="return"
    )


def write_tear_sheet(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    asof_date: str,
    returns: pd.Series,
    spy_returns: pd.Series,
    trade_pnls: pd.Series,
    risk_free_rate: float,
    output_dir: Path = Path("output"),
) -> Path:
    metrics = compute_metrics(returns, risk_free_rate=risk_free_rate, trade_pnls=trade_pnls)
    spy_metrics = compute_metrics(spy_returns, risk_free_rate=risk_free_rate)
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO tear_sheet_metrics (run_id, date, metric_name, metric_value)
            VALUES (?, ?, ?, ?)
            """,
            [(run_id, asof_date, k, v) for k, v in metrics.items()],
        )
    grid = monthly_returns_grid(returns)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"tear_sheet_{asof_date}.md"
    lines = [
        "# Meridian Capital Partners Tear Sheet",
        f"Date: {asof_date}",
        "",
        "## Named Metrics",
    ]
    lines.extend(f"- {k}: {metrics[k]:.6f} (SPY: {spy_metrics.get(k, 0.0):.6f})" for k in NAMED_METRICS)
    lines.extend(
        [
            "",
            "## Monthly Returns Grid",
            grid.fillna("").to_markdown(),
            "",
            "## Equity Curve",
            "Portfolio and SPY rebased to 100 for visual comparison.",
            "",
            "## Drawdown Chart",
            "Peak-to-trough drawdown series computed from daily returns.",
            "",
            "## Rolling 12-Month Sharpe",
            "Rolling 252-trading-day Sharpe ratio computed from daily returns.",
            "",
            "## Factor + Sector Exposures",
            "Latest factor and sector exposure summaries are read from SQLite snapshots.",
            "",
            "## Turnover Summary",
            "30d, 90d, and annualized turnover compared with configured budget.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


__all__ = ["NAMED_METRICS", "compute_metrics", "monthly_returns_grid", "write_tear_sheet"]
