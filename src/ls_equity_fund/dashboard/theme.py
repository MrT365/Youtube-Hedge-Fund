"""DASH-01 dark theme tokens + Streamlit chrome hider.

Loaded once at the top of app.py via ``apply_theme()``. The CSS string is
injected via ``st.markdown(unsafe_allow_html=True)`` — the canonical Streamlit
theming hook for tokens not exposed by ``[theme]`` in config.toml.

Token sources (DASH-01):
- BG:                 #0b0e17
- Card gradient:      #131827 → #1a2035
- Accent indigo:      #6366f1
- Long (bullish):     #10b981
- Short (bearish):    #f43f5e
- Fonts:              Plus Jakarta Sans (UI), JetBrains Mono (numerics)
"""

from __future__ import annotations

import streamlit as st

BG = "#0b0e17"
CARD_FROM = "#131827"
CARD_TO = "#1a2035"
ACCENT_INDIGO = "#6366f1"
LONG_GREEN = "#10b981"
SHORT_RED = "#f43f5e"
TEXT_PRIMARY = "#e2e8f0"
TEXT_MUTED = "#94a3b8"
BORDER = "#1e293b"

_FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    "family=Plus+Jakarta+Sans:wght@400;500;600;700;800&"
    "family=JetBrains+Mono:wght@400;500;600&display=swap"
    '" rel="stylesheet">'
)

_CSS = f"""
<style>
  /* Base background + text */
  html, body, [data-testid="stAppViewContainer"], .stApp {{
      background: {BG} !important;
      color: {TEXT_PRIMARY};
      font-family: 'Plus Jakarta Sans', system-ui, sans-serif !important;
  }}
  /* Hide Streamlit chrome */
  #MainMenu {{visibility: hidden;}}
  footer {{visibility: hidden;}}
  header[data-testid="stHeader"] {{display: none;}}
  div[data-testid="stToolbar"] {{display: none;}}
  /* Sidebar */
  section[data-testid="stSidebar"] {{
      background: linear-gradient(180deg, {CARD_FROM}, {CARD_TO});
      border-right: 1px solid {BORDER};
  }}
  section[data-testid="stSidebar"] .stMarkdown,
  section[data-testid="stSidebar"] label {{
      color: {TEXT_PRIMARY} !important;
  }}
  /* Headings */
  h1, h2, h3, h4, h5 {{
      font-family: 'Plus Jakarta Sans', sans-serif !important;
      color: {TEXT_PRIMARY};
      letter-spacing: -0.01em;
  }}
  /* Numerics use mono */
  .mono, .meridian-num {{
      font-family: 'JetBrains Mono', monospace !important;
  }}
  /* JARVIS oversized header */
  .jarvis-header {{
      font-family: 'Plus Jakarta Sans', sans-serif;
      font-size: 92px;
      font-weight: 800;
      line-height: 1.0;
      letter-spacing: -0.04em;
      background: linear-gradient(135deg, {ACCENT_INDIGO} 0%, {LONG_GREEN} 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      margin: 0 0 4px 0;
  }}
  .jarvis-subtitle {{
      font-family: 'Plus Jakarta Sans', sans-serif;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.32em;
      color: {TEXT_MUTED};
      text-transform: uppercase;
      margin: 0 0 24px 0;
  }}
  /* Roman-numeral pill nav (DASH-02) */
  .pill-nav {{
      display: flex; gap: 8px; flex-wrap: wrap;
      margin: 0 0 28px 0;
  }}
  .pill {{
      padding: 7px 16px;
      border-radius: 999px;
      border: 1px solid {BORDER};
      background: {CARD_FROM};
      color: {TEXT_MUTED};
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.08em;
      user-select: none;
  }}
  .pill.active {{
      background: linear-gradient(135deg, {ACCENT_INDIGO}, #818cf8);
      color: white;
      border-color: transparent;
      box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
  }}
  /* Metric cards */
  div[data-testid="stMetric"] {{
      background: linear-gradient(135deg, {CARD_FROM}, {CARD_TO});
      border: 1px solid {BORDER};
      border-radius: 10px;
      padding: 14px 18px;
  }}
  div[data-testid="stMetricValue"] {{
      font-family: 'JetBrains Mono', monospace !important;
      color: {TEXT_PRIMARY} !important;
  }}
  div[data-testid="stMetricLabel"] {{
      color: {TEXT_MUTED} !important;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 11px;
  }}
  /* DataFrame styling */
  div[data-testid="stDataFrame"] {{
      border: 1px solid {BORDER};
      border-radius: 8px;
      overflow: hidden;
  }}
  /* Input controls */
  .stSelectbox label, .stSlider label, .stMultiSelect label {{
      color: {TEXT_MUTED} !important;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
  }}
  /* Long / short markers (used inline) */
  .long-tag {{color: {LONG_GREEN}; font-weight: 600;}}
  .short-tag {{color: {SHORT_RED}; font-weight: 600;}}
  /* Status strip badges (DASH-03 / SC2 status row) */
  .status-strip {{
      display: flex; gap: 12px; flex-wrap: wrap;
      margin: 16px 0 24px 0;
      padding: 10px 14px;
      background: linear-gradient(135deg, {CARD_FROM}, {CARD_TO});
      border: 1px solid {BORDER};
      border-radius: 8px;
      align-items: center;
  }}
  .badge {{
      padding: 4px 10px;
      border-radius: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.10em;
  }}
  .badge-label {{
      color: {TEXT_MUTED}; font-size: 11px; letter-spacing: 0.08em;
      text-transform: uppercase; margin-right: 6px;
  }}
  .badge-long  {{ background: rgba(16,185,129,0.18); color: {LONG_GREEN}; }}
  .badge-short {{ background: rgba(244,63,94,0.18);  color: {SHORT_RED};  }}
  .badge-warn  {{ background: rgba(245,158,11,0.18); color: #f59e0b;      }}
  .badge-muted {{ background: rgba(148,163,184,0.18); color: {TEXT_MUTED}; }}
  /* Candidate cards (Page II — DASH-04 / SC3) */
  .cand-card {{
      background: linear-gradient(135deg, {CARD_FROM}, {CARD_TO});
      border: 1px solid {BORDER};
      border-radius: 10px;
      padding: 12px 14px;
      margin: 0;
  }}
  .cand-card .cand-ticker {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: {TEXT_PRIMARY};
  }}
  .cand-card .cand-sector {{
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: {TEXT_MUTED};
      margin-top: 2px;
  }}
  .cand-card .cand-row {{
      display: flex; justify-content: space-between; gap: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      margin-top: 6px;
      color: {TEXT_PRIMARY};
  }}
  .cand-card .cand-row .lbl {{
      color: {TEXT_MUTED}; text-transform: uppercase; letter-spacing: 0.06em;
      font-size: 10px;
  }}
  .cand-card.long-side {{ border-left: 3px solid {LONG_GREEN}; }}
  .cand-card.short-side {{ border-left: 3px solid {SHORT_RED}; }}
  /* Heatmap cell colorisation (Page II) */
  .heatmap-table {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; }}
</style>
"""


def apply_theme() -> None:
    """Inject dark theme + JARVIS styling. Call once near the top of app.py."""
    st.markdown(_FONT_LINK + _CSS, unsafe_allow_html=True)


def jarvis_header(subtitle: str = "Long / Short Hedge Fund Analyst") -> None:
    """Render the JARVIS oversized title block."""
    st.markdown(
        f'<div class="jarvis-header">JARVIS</div><div class="jarvis-subtitle">{subtitle}</div>',
        unsafe_allow_html=True,
    )


PAGES: tuple[str, ...] = (
    "I PORTFOLIO",
    "II RESEARCH",
    "III RISK",
    "IV PERFORMANCE",
    "V EXECUTION",
    "VI LETTER",
)


def pill_nav(active: str = "I PORTFOLIO") -> None:
    """Render the static styled pill nav. The interactive nav (which routes
    on click) is built in app.py via st.session_state + Streamlit buttons —
    this function exists for the rare pages that ship a non-interactive
    surface (e.g. embedded snapshots)."""
    pills = "".join(
        f'<span class="pill{" active" if p == active else ""}">{p}</span>' for p in PAGES
    )
    st.markdown(f'<div class="pill-nav">{pills}</div>', unsafe_allow_html=True)


def status_strip_html(items: list[tuple[str, str, str]]) -> str:
    """Build the HTML for a status strip row.

    items = [(label, value, color_token), ...] where color_token maps to one of
    'long' / 'short' / 'warn' / 'muted'. Used by Page I status row.
    """
    parts = ['<div class="status-strip">']
    for label, value, color in items:
        cls = f"badge badge-{color}"
        parts.append(f'<span class="badge-label">{label}</span><span class="{cls}">{value}</span>')
    parts.append("</div>")
    return "".join(parts)


__all__ = [
    "ACCENT_INDIGO",
    "BG",
    "BORDER",
    "CARD_FROM",
    "CARD_TO",
    "LONG_GREEN",
    "PAGES",
    "SHORT_RED",
    "TEXT_MUTED",
    "TEXT_PRIMARY",
    "apply_theme",
    "jarvis_header",
    "pill_nav",
    "status_strip_html",
]
