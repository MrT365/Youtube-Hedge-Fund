"""refresh_macro_calendar — fetch success / cached fallback / staleness gating.

Coverage map (matches 01-08-PLAN.md acceptance criteria):
  * test_first_run_fetches_and_persists      — happy path, no prior rows
  * test_skips_when_within_refresh_interval  — 7-day refresh-interval gate
  * test_force_overrides_interval            — `force=True` bypasses gate
  * test_fall_back_to_cache_on_network_error — NetworkError → cached rows kept
  * test_fallback_within_7d_warns_at_info    — fresh cache → INFO log only
  * test_fallback_beyond_7d_warns_at_warning — stale cache → WARNING + days_since
  * test_upsert_is_idempotent                — re-running same events does NOT duplicate
  * test_local_tz_field_persisted            — event_date_local round-trips through UPSERT
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.config import load_config
from ls_equity_fund.data.macro_calendar import (
    REFRESH_INTERVAL_DAYS,
    STALENESS_WARN_THRESHOLD_DAYS,
    refresh_macro_calendar,
)
from ls_equity_fund.data.providers.fred_provider import NetworkError


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Per-test isolated SQLite DB — Alembic upgrade head, return (Config, conn)."""
    db = tmp_path / "test.db"

    repo_root = Path(__file__).resolve().parents[3]
    cfg = AlembicConfig(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    alembic_command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    # `load_config` validates required secrets via pydantic-settings; the conftest
    # `isolate_env` autouse fixture strips them between tests, so we re-inject test
    # values here. Tests always pass `conn` explicitly so the Config is just used
    # for downstream attribute access (and to keep refresh_macro_calendar's
    # signature honest).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-do-not-use")
    monkeypatch.setenv("SEC_USER_AGENT", "Meridian Capital Partners test@example.com")
    config_obj, _ = load_config(yaml_path=str(repo_root / "config.yaml.example"))
    config_obj.data.cache_dir = str(tmp_path)
    yield config_obj, conn
    conn.close()


def _events() -> list[dict[str, object]]:
    return [
        {
            "event_id": "abc1abc1abc1abc1",
            "event_type": "FOMC",
            "event_date_et": "2026-06-17",
            "event_date_local": "2026-06-17",
            "description": "FOMC Meeting (June)",
            "source": "federalreserve.gov",
        },
        {
            "event_id": "abc2abc2abc2abc2",
            "event_type": "FOMC",
            "event_date_et": "2026-09-16",
            "event_date_local": "2026-09-16",
            "description": "FOMC Meeting (September)",
            "source": "federalreserve.gov",
        },
    ]


# ---------- happy path ----------


def test_first_run_fetches_and_persists(setup) -> None:
    config_obj, conn = setup
    provider = MagicMock()
    provider.fetch_macro_events.return_value = _events()

    result = refresh_macro_calendar(
        config_obj, conn=conn, today=date(2026, 4, 1), provider=provider
    )

    assert result["events_written"] == 2
    assert result["fell_back"] is False
    assert result["staleness_days"] == 0

    n = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
    assert n == 2
    provider.fetch_macro_events.assert_called_once()


# ---------- 7-day refresh-interval gating ----------


def test_skips_when_within_refresh_interval(setup) -> None:
    config_obj, conn = setup
    three_days_ago = int(time.time()) - 3 * 86400
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_type, event_date_et, "
        "source, fetched_at, last_refreshed) VALUES (?, ?, ?, ?, ?, ?)",
        ("seed", "FOMC", "2026-06-17", "federalreserve.gov", three_days_ago, three_days_ago),
    )
    conn.commit()

    provider = MagicMock()
    result = refresh_macro_calendar(config_obj, conn=conn, today=date.today(), provider=provider)

    assert result["events_written"] == 0
    assert result["fell_back"] is False
    assert result["staleness_days"] == 3
    provider.fetch_macro_events.assert_not_called()


def test_force_overrides_interval(setup) -> None:
    config_obj, conn = setup
    one_day_ago = int(time.time()) - 86400
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_type, event_date_et, "
        "source, fetched_at, last_refreshed) VALUES (?, ?, ?, ?, ?, ?)",
        ("seed", "FOMC", "2026-06-17", "federalreserve.gov", one_day_ago, one_day_ago),
    )
    conn.commit()

    provider = MagicMock()
    provider.fetch_macro_events.return_value = _events()
    result = refresh_macro_calendar(
        config_obj, conn=conn, today=date.today(), provider=provider, force=True
    )

    assert result["events_written"] == 2
    provider.fetch_macro_events.assert_called_once()


# ---------- cached-fallback semantics ----------


def test_fall_back_to_cache_on_network_error(setup) -> None:
    """NetworkError must NOT raise to caller — daily run continues with cache."""
    config_obj, conn = setup
    thirty_ago = int(time.time()) - 30 * 86400
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_type, event_date_et, "
        "source, fetched_at, last_refreshed) VALUES (?, ?, ?, ?, ?, ?)",
        ("seed", "FOMC", "2026-06-17", "federalreserve.gov", thirty_ago, thirty_ago),
    )
    conn.commit()

    provider = MagicMock()
    provider.fetch_macro_events.side_effect = NetworkError("DNS down")

    # Must not raise.
    result = refresh_macro_calendar(config_obj, conn=conn, today=date.today(), provider=provider)

    assert result["fell_back"] is True
    assert result["events_written"] == 0
    assert result["staleness_days"] >= 30
    # Cached row preserved.
    n = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
    assert n == 1


def test_fallback_within_7d_does_not_emit_stale_warning(
    setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cache <7d old + scrape fails → INFO log only, no `macro_calendar_stale_warning`.

    structlog's default PrintLogger writes to stdout, so stale-warning detection
    uses ``capsys`` (stdout/stderr capture) rather than ``caplog``.
    """
    config_obj, conn = setup
    # 5 days ago — fresh enough that no stale warning fires.
    five_ago = int(time.time()) - 5 * 86400
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_type, event_date_et, "
        "source, fetched_at, last_refreshed) VALUES (?, ?, ?, ?, ?, ?)",
        ("seed", "FOMC", "2026-06-17", "federalreserve.gov", five_ago, five_ago),
    )
    conn.commit()

    provider = MagicMock()
    provider.fetch_macro_events.side_effect = NetworkError("DNS down")

    # Note: the 5-day-old row is INSIDE the 7-day refresh interval — so the
    # function returns early via the skip-branch BEFORE attempting the scrape.
    # Force=True bypasses that gate so the fallback path is exercised.
    result = refresh_macro_calendar(
        config_obj, conn=conn, today=date.today(), provider=provider, force=True
    )

    assert result["fell_back"] is True
    assert result["staleness_days"] == 5
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "macro_calendar_stale_warning" not in combined, (
        "stale warning fired even though cache is within threshold"
    )
    # Sanity: the *fetch_failed* event always fires when the scrape blew up.
    assert "macro_calendar_fetch_failed_falling_back" in combined


def test_fallback_beyond_7d_emits_stale_warning(setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Cache >=7d old + scrape fails → WARNING with `macro_calendar_stale_warning`."""
    config_obj, conn = setup
    fifteen_ago = int(time.time()) - 15 * 86400
    conn.execute(
        "INSERT INTO macro_calendar (event_id, event_type, event_date_et, "
        "source, fetched_at, last_refreshed) VALUES (?, ?, ?, ?, ?, ?)",
        ("seed", "FOMC", "2026-06-17", "federalreserve.gov", fifteen_ago, fifteen_ago),
    )
    conn.commit()

    provider = MagicMock()
    provider.fetch_macro_events.side_effect = NetworkError("DNS down")

    result = refresh_macro_calendar(config_obj, conn=conn, today=date.today(), provider=provider)

    assert result["fell_back"] is True
    assert result["staleness_days"] >= STALENESS_WARN_THRESHOLD_DAYS
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "macro_calendar_stale_warning" in combined, (
        f"expected macro_calendar_stale_warning event for cache >=7d old; "
        f"captured output: {combined!r}"
    )
    assert "days_since=15" in combined or "days_since=14" in combined


# ---------- idempotency / UPSERT ----------


def test_upsert_is_idempotent(setup) -> None:
    """Re-running with the same events MUST NOT duplicate rows."""
    config_obj, conn = setup
    provider = MagicMock()
    provider.fetch_macro_events.return_value = _events()

    refresh_macro_calendar(config_obj, conn=conn, today=date(2026, 1, 1), provider=provider)
    n_first = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
    assert n_first == 2

    # Force second pass with the same event_ids.
    refresh_macro_calendar(
        config_obj,
        conn=conn,
        today=date(2026, 1, 1),
        provider=provider,
        force=True,
    )
    n_second = conn.execute("SELECT COUNT(*) FROM macro_calendar").fetchone()[0]
    assert n_second == 2, f"UPSERT duplicated rows: {n_second}"


# ---------- event_date_local persistence (D-19 timezone column) ----------


def test_local_tz_field_persisted(setup) -> None:
    """event_date_local must round-trip through UPSERT for FOMC blackout queries."""
    config_obj, conn = setup
    provider = MagicMock()
    provider.fetch_macro_events.return_value = _events()

    refresh_macro_calendar(config_obj, conn=conn, today=date(2026, 1, 1), provider=provider)
    rows = conn.execute(
        "SELECT event_id, event_date_et, event_date_local FROM macro_calendar "
        "ORDER BY event_date_et"
    ).fetchall()
    assert len(rows) == 2
    for r in rows:
        # Local-TZ column populated (v1 mirrors ET; D-19 future refactor may change it).
        assert r["event_date_local"] is not None
        assert r["event_date_local"] == r["event_date_et"]


# ---------- module-level constants surface ----------


def test_refresh_interval_constant_is_seven() -> None:
    assert REFRESH_INTERVAL_DAYS == 7


def test_staleness_threshold_constant_is_seven() -> None:
    assert STALENESS_WARN_THRESHOLD_DAYS == 7
