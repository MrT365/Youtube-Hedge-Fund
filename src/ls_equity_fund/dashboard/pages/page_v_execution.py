"""Page V Execution dashboard (DASH-07)."""

from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from ls_equity_fund.dashboard.runtime import maybe_execution_poll


def render(conn: sqlite3.Connection, *, market_open: bool = False) -> None:
    maybe_execution_poll(market_open=market_open)
    st.markdown("### V Execution")
    orders = _df(conn, "SELECT * FROM orders ORDER BY timestamp DESC")
    today_count = len(orders)
    filled = int((orders.get("status", pd.Series(dtype=str)).astype(str).str.lower() == "filled").sum()) if not orders.empty else 0
    pending = int(orders.get("status", pd.Series(dtype=str)).astype(str).str.lower().isin(["submitted", "partial"]).sum()) if not orders.empty else 0
    rejected = int((orders.get("status", pd.Series(dtype=str)).astype(str).str.lower() == "rejected").sum()) if not orders.empty else 0
    notional = float((orders.get("shares", pd.Series(dtype=float)).abs() * orders.get("limit_price", pd.Series(dtype=float))).sum()) if not orders.empty else 0.0
    avg_slip = float(pd.to_numeric(orders.get("slippage_bps", pd.Series(dtype=float)), errors="coerce").mean()) if not orders.empty else 0.0
    cols = st.columns(6)
    values: list[str] = [str(today_count), str(filled), str(pending), str(rejected), f"${notional:,.0f}", f"{avg_slip:.2f}"]
    for col, label, value in zip(cols, ["Orders", "Filled", "Pending", "Rejected", "Notional", "Avg slippage"], values, strict=True):
        col.metric(label, value)

    st.markdown("#### Open orders")
    st.dataframe(orders[orders["status"].astype(str).str.lower().isin(["submitted", "partial"])] if not orders.empty else orders, hide_index=True)
    st.markdown("#### Recent trades")
    st.dataframe(orders[orders["status"].astype(str).str.lower() == "filled"].head(200) if not orders.empty else orders, hide_index=True)
    st.markdown("#### Worst-5 fills")
    st.dataframe(orders.sort_values("slippage_bps", ascending=False).head(5) if not orders.empty and "slippage_bps" in orders else pd.DataFrame(), hide_index=True)
    st.markdown("#### Short availability")
    borrow = _df(conn, "SELECT ticker, rate_pct, is_htb, as_of_date, source FROM borrow_rates ORDER BY as_of_date DESC")
    st.dataframe(borrow.assign(flag=borrow["rate_pct"] > 10) if not borrow.empty else borrow, hide_index=True)
    st.markdown("#### Daily notional turnover")
    if not orders.empty:
        chart = orders.assign(day=pd.to_datetime(orders["timestamp"], unit="s").dt.date, notional=orders["shares"].abs() * orders["limit_price"]).groupby("day")["notional"].sum().tail(30)
        st.bar_chart(chart)


def _df(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(sql, conn)
    except Exception:
        return pd.DataFrame()


__all__ = ["render"]
