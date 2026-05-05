"""Macro calendar refresh (DATA-11) — FOMC scrape with cached fallback.

Anti-recommendation rule (CLAUDE.md): NO hardcoded FOMC dates. The live
source is federalreserve.gov via :class:`FedScraperProvider`; cached rows
in the ``macro_calendar`` SQLite table are the v1 fallback when the
scrape fails.

Refresh semantics (plan 01-08):

* Daily orchestrator (Phase 9) calls with ``force=False``. If the most
  recent ``last_refreshed`` is within :data:`REFRESH_INTERVAL_DAYS`, this
  function no-ops (no fetch, no rewrite). The Fed page is polled at
  weekly cadence — daily polling would be wasteful and rude.
* On scrape success: UPSERT events into ``macro_calendar`` with
  ``last_refreshed = now``.
* On scrape failure (``NetworkError``): keep stored rows; if the most
  recent refresh is older than :data:`STALENESS_WARN_THRESHOLD_DAYS`,
  emit a structlog warning (``macro_calendar_stale_warning``) with the
  staleness in days. The dashboard (Phase 10) surfaces this so the
  operator notices, but the daily run NEVER raises here — that would
  prevent the Phase 9 spine from completing.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from typing import Any

import structlog

from ls_equity_fund.config import Config
from ls_equity_fund.data.providers.fred_provider import (
    FedScraperProvider,
    NetworkError,
)
from ls_equity_fund.db import get_connection, get_db_path

log = structlog.get_logger(__name__)

REFRESH_INTERVAL_DAYS = 7
STALENESS_WARN_THRESHOLD_DAYS = 7


def refresh_macro_calendar(
    config: Config,
    conn: sqlite3.Connection | None = None,
    *,
    force: bool = False,
    today: date | None = None,
    provider: Any = None,
) -> dict[str, Any]:
    """Refresh ``macro_calendar`` from fed.gov, with cached fallback.

    Args:
        config: composed runtime config (used for cache_dir → db path).
        conn: optional pre-opened SQLite connection (caller-managed). If
            None, a connection is opened from ``config`` and closed on
            return.
        force: bypass the 7-day refresh-interval gate. Default False.
        today: override "today" (date) for deterministic tests. Default
            ``date.today()``.
        provider: injectable :class:`MacroProvider`-shaped object for
            tests. Default: a freshly-constructed :class:`FedScraperProvider`.

    Returns:
        ``{"events_written": int, "fell_back": bool, "staleness_days": int | None}``.
        ``staleness_days`` is None on first-ever run (no prior rows).
    """
    today = today or date.today()
    owns_conn = conn is None
    if conn is None:
        conn = get_connection(get_db_path(config))
    try:
        last_refreshed_ts = _max_last_refreshed(conn)
        days_since = _days_since(last_refreshed_ts, today)

        # Skip if recently refreshed and not forced.
        if (
            not force
            and last_refreshed_ts is not None
            and days_since is not None
            and days_since < REFRESH_INTERVAL_DAYS
        ):
            log.info(
                "macro_calendar_refresh_skipped",
                reason="within refresh interval",
                days_since=days_since,
            )
            return {
                "events_written": 0,
                "fell_back": False,
                "staleness_days": days_since,
            }

        if provider is None:
            provider = FedScraperProvider()

        try:
            events = provider.fetch_macro_events(lookahead_days=365)
            n = _persist_events(conn, events)
            log.info("macro_calendar_refreshed", events_written=n)
            return {
                "events_written": n,
                "fell_back": False,
                "staleness_days": 0,
            }
        except NetworkError as e:
            log.warning("macro_calendar_fetch_failed_falling_back", error=str(e))
            staleness = days_since if days_since is not None else 9999
            if staleness >= STALENESS_WARN_THRESHOLD_DAYS:
                log.warning(
                    "macro_calendar_stale_warning",
                    days_since=staleness,
                    threshold_days=STALENESS_WARN_THRESHOLD_DAYS,
                )
            return {
                "events_written": 0,
                "fell_back": True,
                "staleness_days": staleness,
            }
    finally:
        if owns_conn:
            conn.close()


def _persist_events(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> int:
    now = int(time.time())
    n = 0
    for ev in events:
        conn.execute(
            """INSERT OR REPLACE INTO macro_calendar
               (event_id, event_type, event_date_et, event_date_local,
                description, source, fetched_at, last_refreshed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ev["event_id"],
                ev["event_type"],
                ev["event_date_et"],
                ev.get("event_date_local"),
                ev.get("description"),
                ev["source"],
                now,
                now,
            ),
        )
        n += 1
    return n


def _max_last_refreshed(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(last_refreshed) FROM macro_calendar").fetchone()
    if row is None:
        return None
    val = row[0]
    return int(val) if val is not None else None


def _days_since(ts: int | None, today: date) -> int | None:
    if ts is None:
        return None
    last_date = date.fromtimestamp(ts)
    return (today - last_date).days


__all__ = [
    "REFRESH_INTERVAL_DAYS",
    "STALENESS_WARN_THRESHOLD_DAYS",
    "refresh_macro_calendar",
]
