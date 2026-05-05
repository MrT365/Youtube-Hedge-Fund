"""Dashboard runtime helpers for Phase 10."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

HEARTBEAT_MAX_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class HeartbeatStatus:
    status: str
    message: str
    timestamp: str | None
    exit_code: int | None


def heartbeat_status(path: Path, *, now: datetime | None = None) -> HeartbeatStatus:
    now = now or datetime.now(UTC)
    if not path.exists():
        return HeartbeatStatus("red", "Daily run stale — no heartbeat file found", None, None)
    text = path.read_text(encoding="utf-8").strip()
    parts = dict(part.split("=", 1) for part in text.split() if "=" in part)
    ts_text = parts.get("completed_at")
    exit_code = int(parts.get("exit_code", "1"))
    if ts_text is None:
        return HeartbeatStatus("red", "Daily run stale — malformed heartbeat", None, exit_code)
    try:
        ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
    except ValueError:
        return HeartbeatStatus("red", "Daily run stale — malformed timestamp", ts_text, exit_code)
    age = (now - ts).total_seconds()
    if exit_code != 0 or age > HEARTBEAT_MAX_AGE_SECONDS:
        return HeartbeatStatus("red", f"Daily run stale — last completed {ts_text}", ts_text, exit_code)
    return HeartbeatStatus("green", f"Daily run current — last completed {ts_text}", ts_text, exit_code)


def render_heartbeat_banner(path: Path) -> HeartbeatStatus:
    status = heartbeat_status(path)
    if status.status == "red":
        st.error(status.message)
    else:
        st.success(status.message)
    return status


def maybe_auto_refresh(*, market_open: bool, seconds: int = 300) -> None:
    if market_open:
        st.markdown(f'<meta http-equiv="refresh" content="{seconds}">', unsafe_allow_html=True)


def maybe_execution_poll(*, market_open: bool, seconds: int = 30) -> None:
    if market_open:
        st.markdown(f'<meta http-equiv="refresh" content="{seconds}">', unsafe_allow_html=True)


__all__ = ["HeartbeatStatus", "heartbeat_status", "maybe_auto_refresh", "maybe_execution_poll", "render_heartbeat_banner"]
