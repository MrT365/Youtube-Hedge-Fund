"""Page I additions: heartbeat banner and JARVIS chat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from ls_equity_fund.dashboard.jarvis_chat import SESSION_COST_LIMIT_USD, ask_jarvis
from ls_equity_fund.dashboard.runtime import render_heartbeat_banner


def render_heartbeat(cache_dir: Path = Path("cache")) -> None:
    render_heartbeat_banner(cache_dir / "last_run_completed.txt")


def render_jarvis_chat(*, snapshot_path: Path = Path("cache/jarvis_snapshot.json"), client: Any | None = None) -> None:
    st.sidebar.markdown("### JARVIS")
    history = st.session_state.setdefault("jarvis_history", [])
    cost = float(st.session_state.setdefault("jarvis_session_cost", 0.0))
    st.sidebar.caption(f"Session cost: ${cost:.4f}")
    if cost > SESSION_COST_LIMIT_USD:
        st.sidebar.error("Session limit reached")
        return
    prompt = st.chat_input("Ask JARVIS")
    if not prompt:
        return
    history.append({"role": "user", "content": prompt})
    if client is None:
        response = "JARVIS is ready. Configure a Claude client for live chat; dashboard auto-refresh never calls Claude."
    else:
        result = ask_jarvis(client=client, snapshot_path=snapshot_path, history=history[-10:], user_message=prompt, session_cost_usd=cost)
        st.session_state["jarvis_session_cost"] = result.session_cost_usd
        response = result.text
    history.append({"role": "assistant", "content": response})
    st.session_state["jarvis_history"] = history[-10:]
    st.chat_message("assistant").write(response)


__all__ = ["render_heartbeat", "render_jarvis_chat"]
