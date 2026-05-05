"""Phase 3 dashboard skeleton — read-only view over the L2 scoring engine.

DASH-01 + DASH-02 + DASH-03 (light scope) +
- Top-N ranked candidates by combined score
- Per-ticker × per-factor breakdown table + heatmap
- Sector distribution chart
- Sidebar filters (sector, min score, top-N, asof date)
- JARVIS persona

v1 = display only. No interactive rebalancing, no Claude chat — those land in
Phase 10. Pages III-VI render as placeholders so the Roman-numeral nav reflects
the eventual full surface.

Run locally::

    streamlit run src/ls_equity_fund/dashboard/app.py

Default port 8502 (DASH-01) — can be set via Streamlit's --server.port flag or
the project's launch wrapper. The dashboard reads ``cache/ls_equity_fund.db``;
populate it with ``meridian run-data`` + ``meridian run-scoring`` first.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import TypedDict

import pandas as pd
import streamlit as st

from ls_equity_fund.config import load_config
from ls_equity_fund.dashboard import queries
from ls_equity_fund.dashboard.theme import (
    ACCENT_INDIGO,
    apply_theme,
    jarvis_header,
    pill_nav,
)
from ls_equity_fund.db import get_db_path


class _Filters(TypedDict):
    asof: date
    sectors: list[str]
    min_score: float | None
    top_n: int


# --- Streamlit page config (must be first Streamlit call) -------------------

st.set_page_config(
    page_title="JARVIS — L/S Hedge Fund Analyst",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_theme()


# --- Connection (cached for the session) ------------------------------------


@st.cache_resource(show_spinner=False)
def _connect() -> sqlite3.Connection:
    """Open the SQLite DB once per Streamlit session.

    Uses ``check_same_thread=False`` so Streamlit's executor can reuse the
    connection across reruns; we read only, so thread safety is fine.
    """
    config_path = Path("config.yaml")
    env_path = Path(".env")
    if not config_path.exists() or not env_path.exists():
        return _connect_fallback()
    config, _secrets = load_config(yaml_path=config_path, env_path=env_path)
    db_path = get_db_path(config)
    if not db_path.exists():
        return _connect_fallback()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_fallback() -> sqlite3.Connection:
    """In-memory empty DB so the dashboard renders even with no data yet."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(show_spinner=False, ttl=60)
def _latest_date_cached(_conn_id: int) -> date | None:
    return queries.latest_score_date(_connect())


@st.cache_data(show_spinner=False, ttl=60)
def _sectors_cached(_conn_id: int, asof_iso: str) -> list[str]:
    return queries.available_sectors(_connect(), date.fromisoformat(asof_iso))


@st.cache_data(show_spinner=False, ttl=60)
def _top_candidates_cached(
    _conn_id: int,
    asof_iso: str,
    top: int,
    sectors_key: tuple[str, ...] | None,
    min_score: float | None,
) -> pd.DataFrame:
    sectors = list(sectors_key) if sectors_key else None
    return queries.top_candidates(
        _connect(),
        date.fromisoformat(asof_iso),
        top=top,
        sectors=sectors,
        min_score=min_score,
    )


@st.cache_data(show_spinner=False, ttl=60)
def _factor_breakdown_cached(
    _conn_id: int, asof_iso: str, tickers_key: tuple[str, ...]
) -> pd.DataFrame:
    return queries.factor_breakdown(_connect(), date.fromisoformat(asof_iso), list(tickers_key))


# --- Sidebar ----------------------------------------------------------------


def _render_sidebar(asof: date | None) -> _Filters:
    """Render filters; return chosen values."""
    st.sidebar.markdown(
        '<div style="font-size:11px;font-weight:700;letter-spacing:0.32em;'
        'color:#94a3b8;text-transform:uppercase;margin:8px 0 16px 0;">'
        "Filters</div>",
        unsafe_allow_html=True,
    )

    # asof date — preselects latest, allows operator to backfill view
    if asof is None:
        st.sidebar.warning("No scored data yet. Run `meridian run-scoring` first.")
        asof_choice = date.today()
    else:
        asof_choice = st.sidebar.date_input("As-of date", value=asof, max_value=date.today())

    sectors = _sectors_cached(id(_connect()), asof_choice.isoformat())
    chosen_sectors = st.sidebar.multiselect(
        "Sector",
        options=sectors,
        default=[],
        help="Empty = all sectors",
    )

    min_score = st.sidebar.slider(
        "Min combined score",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=5.0,
        help="Filter candidates with combined percentile below this value",
    )

    top_n = st.sidebar.slider(
        "Top N",
        min_value=5,
        max_value=100,
        value=20,
        step=5,
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown(
        f'<div style="font-size:11px;color:#94a3b8;letter-spacing:0.08em;">'
        f"Score date<br/>"
        f'<span style="font-family:JetBrains Mono,monospace;color:#e2e8f0;'
        f'font-size:14px;">{asof_choice.isoformat()}</span></div>',
        unsafe_allow_html=True,
    )

    return {
        "asof": asof_choice,
        "sectors": chosen_sectors,
        "min_score": min_score if min_score > 0 else None,
        "top_n": top_n,
    }


# --- Main page renderers ----------------------------------------------------


def _format_score(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.1f}"


def _render_top_candidates(df: pd.DataFrame) -> None:
    st.markdown("### Top Ranked Candidates")
    if df.empty:
        st.info(
            "No candidates match the current filter. Loosen the filters in the "
            "sidebar, or run `meridian run-scoring` to populate scores."
        )
        return

    display = df.copy()
    display["combined_score"] = display["combined_score"].map(_format_score)
    display = display.rename(
        columns={
            "rank": "Rank",
            "ticker": "Ticker",
            "sector": "Sector",
            "combined_score": "Combined",
            "n_subfactors_used": "Sub-factors",
        }
    )
    st.dataframe(display, width="stretch", hide_index=True)


def _render_factor_breakdown(df: pd.DataFrame) -> None:
    st.markdown("### Factor Breakdown")
    if df.empty:
        st.info("No factor data available for the selected candidates.")
        return

    display = df.copy()
    factor_cols = [c for c in display.columns if c != "ticker"]

    # Streamlit's column_config for inline progress bars per factor cell.
    # Each cell is a 0-100 percentile.
    column_config = {
        col: st.column_config.ProgressColumn(
            col.replace("_", " ").title(),
            min_value=0,
            max_value=100,
            format="%.0f",
            help=f"Sector-percentile rank for {col}",
        )
        for col in factor_cols
    }
    column_config["ticker"] = st.column_config.TextColumn("Ticker")

    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config=column_config,
    )


def _render_sector_distribution(df: pd.DataFrame) -> None:
    st.markdown("### Sector Distribution (Top Candidates)")
    if df.empty:
        st.info("No sector data available — run scoring or loosen filters.")
        return

    chart_df = df.set_index("sector")[["count"]]
    st.bar_chart(chart_df, color=ACCENT_INDIGO, width="stretch")

    # Average-score per sector beneath the chart
    sector_table = df.copy()
    sector_table["avg_score"] = sector_table["avg_score"].map(_format_score)
    sector_table = sector_table.rename(
        columns={"sector": "Sector", "count": "Tickers", "avg_score": "Avg Score"}
    )
    st.dataframe(sector_table, width="stretch", hide_index=True)


def _render_summary_metrics(
    asof: date | None, total_universe: int, total_scored: int, top_df: pd.DataFrame
) -> None:
    cols = st.columns(4)
    cols[0].metric("Universe", f"{total_universe:,}")
    cols[1].metric("Scored", f"{total_scored:,}")
    if not top_df.empty:
        avg = top_df["combined_score"].mean()
        cols[2].metric("Top-N Avg Score", f"{avg:.1f}")
        cols[3].metric("Sectors in Top-N", f"{top_df['sector'].nunique()}")
    else:
        cols[2].metric("Top-N Avg Score", "—")
        cols[3].metric("Sectors in Top-N", "—")


def _render_placeholder_pages() -> None:
    """DASH-02 — render Pages III-VI as placeholders so the nav surface is real."""
    st.markdown("---")
    st.markdown(
        '<div style="color:#94a3b8;font-size:12px;letter-spacing:0.08em;">'
        "Pages <span style='font-family:JetBrains Mono,monospace;'>III</span> Risk · "
        "<span style='font-family:JetBrains Mono,monospace;'>IV</span> Performance · "
        "<span style='font-family:JetBrains Mono,monospace;'>V</span> Execution · "
        "<span style='font-family:JetBrains Mono,monospace;'>VI</span> Letter "
        "ship in Phase 10.</div>",
        unsafe_allow_html=True,
    )


# --- Main entrypoint --------------------------------------------------------


def main() -> None:
    jarvis_header()
    pill_nav(active="II RESEARCH")

    conn = _connect()
    conn_id = id(conn)
    asof = _latest_date_cached(conn_id)

    if asof is None:
        st.warning(
            "**No scoring data found.** "
            "Run `meridian run-data` then `meridian run-scoring` to populate "
            "`cache/ls_equity_fund.db`. The dashboard renders the chrome and "
            "filter sidebar so layout work isn't blocked, but tables stay empty."
        )

    filters = _render_sidebar(asof)
    asof_choice = filters["asof"]

    # Pull data
    sectors_key = tuple(sorted(filters["sectors"])) if filters["sectors"] else None
    top_df = _top_candidates_cached(
        conn_id,
        asof_choice.isoformat(),
        filters["top_n"],
        sectors_key,
        filters["min_score"],
    )
    breakdown_tickers = tuple(top_df["ticker"].tolist()) if not top_df.empty else ()
    breakdown_df = _factor_breakdown_cached(conn_id, asof_choice.isoformat(), breakdown_tickers)
    sector_df = queries.sector_distribution(
        conn,
        asof_choice,
        top=filters["top_n"],
        min_score=filters["min_score"],
    )

    # Layout
    universe_n = queries.universe_size(conn)
    scored_n = queries.scored_size(conn, asof_choice)
    _render_summary_metrics(asof_choice, universe_n, scored_n, top_df)

    st.markdown("")  # spacer

    left, right = st.columns([3, 2])
    with left:
        _render_top_candidates(top_df)
    with right:
        _render_sector_distribution(sector_df)

    st.markdown("")
    _render_factor_breakdown(breakdown_df)

    _render_placeholder_pages()


# Streamlit entrypoint — call main() at module load (Streamlit re-imports this
# file on every rerun). No __name__ guard because Streamlit runs the file as
# its main script, not via -m.
main()
