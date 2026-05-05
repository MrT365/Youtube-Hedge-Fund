"""Page III Risk dashboard (DASH-05)."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd
import streamlit as st

BREAKER_THRESHOLDS = {
    "DAILY_LOSS": -0.015,
    "DAILY_LOSS_HARD": -0.025,
    "WEEKLY_LOSS": -0.04,
    "DRAWDOWN": -0.08,
    "SINGLE_POSITION": 0.03,
}


def mctr_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    avg = pd.to_numeric(out.get("mctr", pd.Series(dtype=float)), errors="coerce").abs().mean()
    out["disproportionate_risk"] = pd.to_numeric(out.get("mctr", 0), errors="coerce").abs() > 2 * (avg or 0)
    return out


def circuit_breaker_status(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = _df(conn, "SELECT breaker_type, threshold, observed_value, timestamp FROM circuit_breaker_log ORDER BY timestamp DESC")
    latest = {str(row.breaker_type): row for row in rows.itertuples(index=False)} if not rows.empty else {}
    out = []
    for name, threshold in BREAKER_THRESHOLDS.items():
        row = latest.get(name)
        observed = float(getattr(row, "observed_value", 0.0)) if row else 0.0
        status = "TRIGGERED" if row else ("WARNING" if abs(observed) >= abs(threshold) * 0.75 else "OK")
        out.append({"breaker": name, "threshold": threshold, "current": observed, "status": status})
    return pd.DataFrame(out)


def render(conn: sqlite3.Connection) -> None:
    st.markdown("### III Risk")
    breakers = circuit_breaker_status(conn)
    st.markdown("#### Circuit breakers")
    st.dataframe(breakers, width="stretch", hide_index=True)

    returns = _df(conn, "SELECT daily_return FROM daily_attribution ORDER BY date DESC LIMIT 252")
    vals = pd.to_numeric(returns.get("daily_return", pd.Series(dtype=float)), errors="coerce").dropna()
    var95 = float(vals.quantile(0.05)) if not vals.empty else 0.0
    cvar95 = float(vals[vals <= var95].mean()) if not vals.empty and not vals[vals <= var95].empty else 0.0
    max_dd = float(vals.min()) if not vals.empty else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("VaR 95%", f"{var95:.2%}")
    c2.metric("CVaR 95%", f"{cvar95:.2%}")
    c3.metric("Max-DD current", f"{max_dd:.2%}")

    risk = _df(conn, "SELECT ticker, factor_variance, specific_variance, total_variance, mctr FROM risk_snapshots ORDER BY timestamp DESC")
    factor_var = float(pd.to_numeric(risk.get("factor_variance", pd.Series(dtype=float)), errors="coerce").sum())
    specific_var = float(pd.to_numeric(risk.get("specific_variance", pd.Series(dtype=float)), errors="coerce").sum())
    total_var = factor_var + specific_var
    st.markdown("#### Risk decomposition")
    st.dataframe(pd.DataFrame([{"factor_variance_pct": _pct(factor_var, total_var), "specific_variance_pct": _pct(specific_var, total_var), "total_variance": total_var}]), hide_index=True)

    st.markdown("#### MCTR")
    st.dataframe(mctr_flags(risk[["ticker", "mctr"]] if not risk.empty else pd.DataFrame(columns=["ticker", "mctr"])), width="stretch", hide_index=True)

    st.markdown("#### Factor exposures")
    exposure = _df(conn, "SELECT factor, exposure FROM factor_exposures ORDER BY factor")
    if exposure.empty:
        exposure = pd.DataFrame({"factor": ["momentum", "value", "quality", "growth", "revisions", "short_interest", "insider", "institutional"], "exposure": [0.0] * 8})
    sigma = pd.to_numeric(exposure["exposure"], errors="coerce").std() or 1.0
    exposure["warning"] = pd.to_numeric(exposure["exposure"], errors="coerce").abs() > 1.5 * sigma
    st.bar_chart(exposure.set_index("factor")["exposure"])
    st.dataframe(exposure, hide_index=True)

    st.markdown("#### Six-scenario stress test")
    scenarios = pd.DataFrame(
        [
            ("rates +100bps", -0.01),
            ("rates -100bps", 0.005),
            ("SPY -10%", -0.10),
            ("SPY -20%", -0.20),
            ("VIX +15", -0.04),
            ("credit spread +200bps", -0.03),
        ],
        columns=["scenario", "estimated_pnl_impact"],
    )
    st.dataframe(scenarios, hide_index=True)

    st.markdown("#### Correlation heatmap + effective bets")
    positions = _df(conn, "SELECT ticker, shares, current_price FROM portfolio_positions")
    weights = pd.to_numeric(positions.get("shares", pd.Series(dtype=float)), errors="coerce").abs()
    hhi = float(((weights / weights.sum()) ** 2).sum()) if weights.sum() else 0.0
    st.metric("Effective bets", f"{(1 / hhi) if hhi else 0:.1f}")
    st.dataframe(pd.DataFrame(1.0, index=positions.get("ticker", pd.Series(dtype=str)), columns=positions.get("ticker", pd.Series(dtype=str))) if not positions.empty else pd.DataFrame())

    st.markdown("#### 72hr alerts")
    cutoff = int(time.time()) - 72 * 3600
    alerts = _df(conn, "SELECT timestamp, breaker_type, observed_value FROM circuit_breaker_log WHERE timestamp >= ? ORDER BY timestamp DESC", (cutoff,))
    st.dataframe(alerts, hide_index=True)


def _pct(value: float, total: float) -> float:
    return value / total if total else 0.0


def _df(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> pd.DataFrame:
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


__all__ = ["circuit_breaker_status", "mctr_flags", "render"]
