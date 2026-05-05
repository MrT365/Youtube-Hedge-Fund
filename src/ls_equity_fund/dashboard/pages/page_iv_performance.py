"""Page IV Performance dashboard (DASH-06)."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st


def render(conn: sqlite3.Connection) -> None:
    st.markdown("### IV Performance")
    attr = _df(conn, "SELECT * FROM daily_attribution ORDER BY date")
    if attr.empty:
        st.info("No daily attribution rows yet. Run `meridian run-reporting`.")
        return
    attr["date"] = pd.to_datetime(attr["date"])
    equity = (1 + pd.to_numeric(attr["daily_return"], errors="coerce").fillna(0)).cumprod() * 100
    spy = (1 + pd.to_numeric(attr["spy_return"], errors="coerce").fillna(0)).cumprod() * 100
    st.markdown("#### Equity curve vs SPY")
    st.line_chart(pd.DataFrame({"Portfolio": equity.values, "SPY": spy.values}, index=attr["date"]))

    st.markdown("#### Monthly returns heatmap")
    monthly = attr.set_index("date")["daily_return"].resample("ME").apply(lambda s: (1 + s).prod() - 1)
    grid = monthly.to_frame("return").assign(year=lambda x: x.index.year, month=lambda x: x.index.month).pivot(index="year", columns="month", values="return")
    st.dataframe(grid.style.background_gradient(cmap="RdYlGn", axis=None), width="stretch")

    st.markdown("#### Drawdown")
    dd = equity / equity.cummax() - 1
    st.area_chart(pd.DataFrame({"drawdown": dd.values}, index=attr["date"]))

    st.markdown("#### P&L attribution")
    st.bar_chart(attr.set_index("date")[["beta_return", "sector_return", "factor_return", "alpha_return"]])

    st.markdown("#### Rolling 12-month Sharpe")
    roll = attr.set_index("date")["daily_return"].rolling(252, min_periods=2).apply(lambda x: x.mean() / x.std() * (252 ** 0.5) if x.std() else 0)
    st.line_chart(roll)

    st.markdown("#### Sector-relative alpha")
    sector_alpha = _df(conn, "SELECT sector, SUM(realized_pnl) AS total_alpha FROM position_attribution GROUP BY sector")
    st.dataframe(sector_alpha, hide_index=True)

    st.markdown("#### Turnover / transaction costs")
    orders = _df(conn, "SELECT date(timestamp, 'unixepoch') AS day, ABS(shares * limit_price) AS notional, slippage_bps FROM orders")
    if not orders.empty:
        st.metric("30d notional", f"${orders['notional'].sum():,.0f}")
        st.metric("Avg slippage bps", f"{orders['slippage_bps'].mean():.2f}")

    st.markdown("#### Best / worst 5 positions")
    pos = _df(conn, "SELECT ticker, side, realized_pnl, holding_bucket, sector FROM position_attribution ORDER BY realized_pnl DESC")
    left, right = st.columns(2)
    left.dataframe(pos.head(5), hide_index=True)
    right.dataframe(pos.tail(5).sort_values("realized_pnl"), hide_index=True)

    st.markdown("#### Win/loss")
    if not pos.empty:
        st.dataframe(pos.assign(won=pos["realized_pnl"] > 0).groupby(["side", "holding_bucket", "sector"], dropna=False)["won"].agg(["count", "mean"]).reset_index(), hide_index=True)

    st.markdown("#### Claude weekly commentary")
    comment = _df(conn, "SELECT week_ending, body_md, generated_at, cached FROM weekly_commentary ORDER BY week_ending DESC LIMIT 1")
    if comment.empty:
        st.info("No cached commentary. `run-reporting` precomputes this; dashboard never calls Claude.")
    else:
        st.markdown(str(comment.iloc[0]["body_md"]))


def _df(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(sql, conn)
    except Exception:
        return pd.DataFrame()


__all__ = ["render"]
