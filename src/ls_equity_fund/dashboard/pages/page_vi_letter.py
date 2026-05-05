"""Page VI daily letter dashboard (DASH-08)."""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import streamlit as st

from ls_equity_fund.reporting.daily_letter import MANDATORY_DISCLAIMER, generate_daily_letter


def latest_letter(conn: sqlite3.Connection, *, day: date, mode: str) -> pd.Series | None:
    try:
        df = pd.read_sql_query(
            "SELECT * FROM daily_letter WHERE date = ? AND mode = ?",
            conn,
            params=[day.isoformat(), mode],
        )
    except Exception:
        return None
    return df.iloc[0] if not df.empty else None


def render(conn: sqlite3.Connection, *, today: date | None = None) -> None:
    day = today or date.today()
    st.markdown("### VI Letter")
    mode_label = st.radio("Mode", ["LP", "Internal"], horizontal=True)
    mode = "lp" if mode_label == "LP" else "internal"
    row = latest_letter(conn, day=day, mode=mode)
    if st.button("Regenerate", help="User-triggered only; page load and auto-refresh never call Claude."):
        with st.spinner("Regenerating cached daily letter..."):
            body = generate_daily_letter(conn, day=day, mode=mode, client=None, regenerate=True)
        st.success("Regenerated")
        st.markdown(body)
        return
    if row is None:
        st.info("No cached letter for today. Run `meridian run-reporting`.")
        return
    body = str(row["body_md"])
    st.caption(f"Generated at {row['generated_at']} | cached={bool(row['cached'])}")
    if mode == "lp":
        st.markdown("#### CONFIDENTIAL")
        st.markdown("### PAPER")
        st.markdown(body)
        if MANDATORY_DISCLAIMER not in body:
            st.error("Mandatory paper-system disclaimer missing.")
    else:
        st.markdown(body)


__all__ = ["latest_letter", "render"]
