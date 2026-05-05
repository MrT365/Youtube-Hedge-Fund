"""Phase 3 dashboard — DASH-01..DASH-04 + Phase-3 SC2/SC3/SC4.

Routes via session state across 6 Roman-numeral pages. Pages I (Portfolio)
and II (Research) are fully rendered against L1 + L2 SQLite — no API calls,
no factor compute, no Anthropic. Pages III-VI ship as placeholders that
explicitly note their target phase.

Auto-refresh (DASH-09 / SC4): 5-minute meta-refresh wired conditionally on
US-equity market hours (9:30am-4:00pm America/New_York). Outside hours, the
page sits still — placeholder pages can never trigger a downstream API call
because the only refresh path is the browser-side meta tag, and the
dashboard itself reads exclusively from SQLite.

Run::

    streamlit run src/ls_equity_fund/dashboard/app.py
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from datetime import time as _time
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ls_equity_fund.config import load_config
from ls_equity_fund.dashboard import queries
from ls_equity_fund.dashboard.theme import (
    PAGES,
    apply_theme,
    jarvis_header,
    status_strip_html,
)
from ls_equity_fund.db import get_db_path

# Market-hours window per DASH-09 / SC4. Held as ZoneInfo so DST is handled
# correctly without us tracking transitions.
_MARKET_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = _time(9, 30)
_MARKET_CLOSE = _time(16, 0)
_REFRESH_INTERVAL_S = 300


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

    ``check_same_thread=False`` because Streamlit's executor reuses the
    connection across reruns; we read only, so thread-safety holds.
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


# --- Auto-refresh (SC4) -----------------------------------------------------


def _is_market_open(now_et: datetime | None = None) -> bool:
    """Mon-Fri, 09:30 ≤ now < 16:00 in America/New_York. Holidays NOT excluded
    in v1 — calendar lookups belong in Phase 1's macro feed; v1 over-refreshes
    on a few holiday weekdays which is harmless (page reads from SQLite only).
    """
    now = now_et if now_et is not None else datetime.now(tz=_MARKET_TZ)
    if now.weekday() >= 5:
        return False
    return _MARKET_OPEN <= now.time() < _MARKET_CLOSE


def _maybe_meta_refresh() -> None:
    """Inject a meta-refresh tag (5 min) when markets are open. Outside
    market hours, no tag → the page stays still. This keeps placeholder
    pages from ever triggering a downstream Anthropic call (per SC4).
    """
    if _is_market_open():
        st.markdown(
            f'<meta http-equiv="refresh" content="{_REFRESH_INTERVAL_S}">',
            unsafe_allow_html=True,
        )


# --- Sidebar ----------------------------------------------------------------


def _render_sidebar(asof: date | None) -> _Filters:
    st.sidebar.markdown(
        '<div style="font-size:11px;font-weight:700;letter-spacing:0.32em;'
        'color:#94a3b8;text-transform:uppercase;margin:8px 0 16px 0;">'
        "Filters</div>",
        unsafe_allow_html=True,
    )

    if asof is None:
        st.sidebar.warning("No scored data yet. Run `meridian run-scoring` first.")
        asof_choice = date.today()
    else:
        asof_choice = st.sidebar.date_input("As-of date", value=asof, max_value=date.today())

    sectors = _sectors_cached(id(_connect()), asof_choice.isoformat())
    chosen_sectors = st.sidebar.multiselect("Sector", options=sectors, default=[])

    min_score = st.sidebar.slider(
        "Min combined score",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=5.0,
    )

    top_n = st.sidebar.slider("Top N", min_value=5, max_value=100, value=20, step=5)

    st.sidebar.markdown("---")

    # Page picker — drives the main pane
    if "page" not in st.session_state:
        st.session_state["page"] = PAGES[0]
    st.sidebar.markdown(
        '<div style="font-size:11px;font-weight:700;letter-spacing:0.32em;'
        'color:#94a3b8;text-transform:uppercase;margin:0 0 8px 0;">Page</div>',
        unsafe_allow_html=True,
    )
    for page in PAGES:
        if st.sidebar.button(
            page,
            key=f"nav_{page}",
            use_container_width=True,
            type="primary" if st.session_state["page"] == page else "secondary",
        ):
            st.session_state["page"] = page

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


# --- Page I (Portfolio cover) — DASH-03 / SC2 -------------------------------


def _render_page_portfolio(conn: sqlite3.Connection, asof: date) -> None:
    jarvis_header()

    # Status strip (top): VIX regime + data source indicator
    vix = queries.vix_close(conn, asof)
    vix_label, vix_color = queries.vix_regime(vix)
    vix_value = f"{vix:.2f}" if vix is not None else "—"
    provider = queries.data_provider_label(conn)
    st.markdown(
        status_strip_html(
            [
                ("VIX regime", f"{vix_label} ({vix_value})", vix_color),
                ("Data source", provider, "muted"),
                ("Score date", asof.isoformat(), "muted"),
            ]
        ),
        unsafe_allow_html=True,
    )

    # 10 metric cards laid out as 5 + 5 (DASH-03 spec). Each pulls from L1+L2
    # SQLite with no compute, no API call.
    metrics = _gather_page_i_metrics(conn, asof)
    _render_metric_grid(metrics)


def _gather_page_i_metrics(conn: sqlite3.Connection, asof: date) -> list[tuple[str, str]]:
    """The 10 metric cards (DASH-03 / SC2). Order is canonical."""
    universe = queries.universe_size(conn)
    return [
        ("Universe", f"{universe:,}"),
        ("Long Candidates", f"{queries.long_candidate_count(conn, asof):,}"),
        ("Short Candidates", f"{queries.short_candidate_count(conn, asof):,}"),
        ("Positions", f"{queries.position_count(conn):,}"),
        ("Crowding", f"{queries.crowding_count(conn):,}"),
        ("Insider Events", f"{queries.insider_event_count(conn, asof):,}"),
        ("CEO Buys", f"{queries.ceo_buy_count(conn, asof):,}"),
        ("Cluster Buys", f"{queries.cluster_buy_count(conn, asof):,}"),
        ("VIX", _fmt_vix(queries.vix_close(conn, asof))),
        ("Earnings 7d", f"{queries.earnings_in_n_days(conn, asof):,}"),
    ]


def _render_metric_grid(metrics: list[tuple[str, str]]) -> None:
    # 5 + 5 layout
    cols = st.columns(5)
    for i, (label, value) in enumerate(metrics[:5]):
        cols[i].metric(label, value)
    cols2 = st.columns(5)
    for i, (label, value) in enumerate(metrics[5:]):
        cols2[i].metric(label, value)


def _fmt_vix(vix: float | None) -> str:
    return f"{vix:.2f}" if vix is not None else "—"


# --- Page II (Research) — DASH-04 / SC3 -------------------------------------


def _render_page_research(conn: sqlite3.Connection, asof: date, filters: _Filters) -> None:
    st.markdown("### Research")
    st.markdown(
        '<div style="color:#94a3b8;font-size:12px;letter-spacing:0.04em;">'
        "Page II reads exclusively from L1 + L2 SQLite — no Anthropic, no factor compute. "
        "Phase 4 Claude analysis lands on Pages I/II/IV under expandable cards."
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # KPIs (top of page)
    universe = queries.universe_size(conn)
    scored = queries.scored_size(conn, asof)
    long_n = queries.long_candidate_count(conn, asof)
    short_n = queries.short_candidate_count(conn, asof)
    sector_count = len(queries.available_sectors(conn, asof))

    cols = st.columns(5)
    cols[0].metric("Universe", f"{universe:,}")
    cols[1].metric("Scored", f"{scored:,}")
    cols[2].metric("Sectors", f"{sector_count}")
    cols[3].metric("Long ≥80", f"{long_n:,}")
    cols[4].metric("Short ≤20", f"{short_n:,}")

    st.markdown("")

    # Crowding warnings
    st.markdown("#### Crowding warnings (DATA-07 — 13F multi-fund openings)")
    crowding = queries.crowding_warnings(conn)
    if crowding.empty:
        st.info(
            "No crowded names — needs ≥3 tracked funds opening NEW positions in the "
            "latest 13F period. Run `meridian run-data` to populate."
        )
    else:
        st.dataframe(crowding, width="stretch", hide_index=True)

    st.markdown("")

    # Factor heatmap — top 30 + bottom 30, all 8 base factors + combined
    st.markdown("#### Factor heatmap — top 30 (longs) + bottom 30 (shorts), 8 base factors")
    heatmap = queries.factor_heatmap(conn, asof, top=30, bottom=30)
    if heatmap.empty:
        st.info("No factor data on the selected date.")
    else:
        # Show the wide-form table with inline progress bars per factor cell.
        # Streamlit's column_config.ProgressColumn renders 0-100 as a bar.
        factor_cols = [c for c in heatmap.columns if c != "ticker"]
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
            heatmap,
            width="stretch",
            hide_index=True,
            column_config=column_config,
            height=min(35 * len(heatmap) + 50, 800),
        )

    st.markdown("")

    # 10 long + 10 short candidate cards with Piotroski + Altman
    st.markdown("#### 10 long + 10 short candidates — Piotroski / Altman")
    long_df = queries.candidate_cards(conn, asof, side="long", n=10)
    short_df = queries.candidate_cards(conn, asof, side="short", n=10)

    left, right = st.columns(2)
    with left:
        st.markdown(
            '<div class="badge-label">Long candidates (top 10 by combined)</div>',
            unsafe_allow_html=True,
        )
        _render_candidate_grid(long_df, side="long")
    with right:
        st.markdown(
            '<div class="badge-label">Short candidates (bottom 10 by combined)</div>',
            unsafe_allow_html=True,
        )
        _render_candidate_grid(short_df, side="short")


def _render_candidate_grid(df: pd.DataFrame, *, side: str) -> None:
    """Render N candidate cards in a 2-col grid using inline HTML."""
    if df.empty:
        st.info("No candidates on the selected date.")
        return

    # 2-col grid via Streamlit columns; each card is a styled HTML block.
    rows = []
    for _, row in df.iterrows():
        rows.append(_candidate_card_html(row, side=side))
    # Pair up rows as 2-col grid
    for i in range(0, len(rows), 2):
        c1, c2 = st.columns(2)
        c1.markdown(rows[i], unsafe_allow_html=True)
        if i + 1 < len(rows):
            c2.markdown(rows[i + 1], unsafe_allow_html=True)


def _candidate_card_html(row: pd.Series, *, side: str) -> str:
    """Build the HTML for one candidate card."""
    side_class = "long-side" if side == "long" else "short-side"
    score = row.get("combined_score")
    score_str = f"{score:.1f}" if score is not None and not pd.isna(score) else "—"
    piotroski = row.get("piotroski_f")
    piot_str = f"{int(piotroski)}/9" if not pd.isna(piotroski) else "—"
    altman = row.get("altman_z")
    if pd.isna(altman) or altman is None:
        alt_str = "—"
    else:
        zone = row.get("altman_zone") or ""
        alt_str = f"{altman:.2f} ({zone})" if zone else f"{altman:.2f}"
    sector = (row.get("sector") or "").upper() or "—"
    return (
        f'<div class="cand-card {side_class}">'
        f'  <div class="cand-ticker">{row["ticker"]}</div>'
        f'  <div class="cand-sector">{sector}</div>'
        f'  <div class="cand-row"><span class="lbl">Combined</span><span>{score_str}</span></div>'
        f'  <div class="cand-row"><span class="lbl">Piotroski F</span><span>{piot_str}</span></div>'
        f'  <div class="cand-row"><span class="lbl">Altman Z</span><span>{alt_str}</span></div>'
        f"</div>"
    )


# --- Pages III..VI — placeholders (SC1) -------------------------------------


def _render_placeholder(page_name: str, target_phase: str) -> None:
    st.markdown(f"### {page_name}")
    st.info(
        f"Page {page_name.split(' ', 1)[0]} ships in **{target_phase}**. The Roman-numeral "
        f"nav reflects the eventual full surface; this placeholder is intentional and "
        f"reads no data — it never triggers an Anthropic call."
    )
    st.markdown(
        '<div style="color:#94a3b8;font-size:12px;letter-spacing:0.04em;">'
        "Pages I (Portfolio) and II (Research) are fully wired. Pages III-VI fill out "
        "as the underlying layers land:</div>"
        "<ul style='color:#94a3b8;font-size:12px;'>"
        "<li><b>III RISK</b> — Phase 6 (L5 risk model + 8-veto + circuit breakers)</li>"
        "<li><b>IV PERFORMANCE</b> — Phase 9 (P&amp;L attribution + tear sheet)</li>"
        "<li><b>V EXECUTION</b> — Phase 8 (IBKR paper, order/slippage tables)</li>"
        "<li><b>VI LETTER</b> — Phase 9/10 (dual-mode LP / internal letter)</li>"
        "</ul>",
        unsafe_allow_html=True,
    )


# --- Main entrypoint --------------------------------------------------------


def main() -> None:
    # SC4: 5-min auto-refresh ONLY during market hours, before any other rendering.
    _maybe_meta_refresh()

    conn = _connect()
    asof = _latest_date_cached(id(conn))
    if asof is None:
        # Sidebar still renders; main pane shows the no-data warning.
        filters = _render_sidebar(asof)
        jarvis_header()
        st.warning(
            "**No scoring data found.** Run `meridian run-data` then `meridian run-scoring` "
            "to populate `cache/ls_equity_fund.db`. The dashboard chrome renders so layout "
            "work isn't blocked, but tables stay empty."
        )
        return

    filters = _render_sidebar(asof)

    page = st.session_state.get("page", PAGES[0])

    if page == "I PORTFOLIO":
        _render_page_portfolio(conn, filters["asof"])
    elif page == "II RESEARCH":
        _render_page_research(conn, filters["asof"], filters)
    elif page == "III RISK":
        jarvis_header()
        _render_placeholder("III RISK", "Phase 6 (L5 Risk Management)")
    elif page == "IV PERFORMANCE":
        jarvis_header()
        _render_placeholder("IV PERFORMANCE", "Phase 9 (Reporting Full)")
    elif page == "V EXECUTION":
        jarvis_header()
        _render_placeholder("V EXECUTION", "Phase 8 (IBKR Paper)")
    elif page == "VI LETTER":
        jarvis_header()
        _render_placeholder("VI LETTER", "Phase 9 / 10 (Daily Letter)")
    else:  # pragma: no cover — defensive
        st.error(f"Unknown page: {page!r}")


main()
