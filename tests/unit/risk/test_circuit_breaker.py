"""Circuit breaker tests."""

from __future__ import annotations

import sqlite3

from ls_equity_fund.risk.circuit_breaker import (
    PortfolioState,
    evaluate_circuit_breakers,
    fire_circuit_breakers,
)


def _types(state: PortfolioState) -> list[str]:
    return [event.breaker_type for event in evaluate_circuit_breakers(state)]


def test_daily_loss_soft_threshold_boundary() -> None:
    assert "daily_loss" not in _types(PortfolioState(nav=1_000_000, daily_pnl_pct=-0.015))
    assert "daily_loss" in _types(PortfolioState(nav=1_000_000, daily_pnl_pct=-0.0151))


def test_daily_loss_hard_threshold_boundary() -> None:
    assert "daily_loss_hard" not in _types(PortfolioState(nav=1_000_000, daily_pnl_pct=-0.025))
    events = evaluate_circuit_breakers(PortfolioState(nav=1_000_000, daily_pnl_pct=-0.0251))
    assert events[0].breaker_type == "daily_loss_hard"
    assert events[0].action == "CLOSE_ALL_TODAY"


def test_weekly_loss_threshold_boundary() -> None:
    assert "weekly_loss" not in _types(PortfolioState(nav=1_000_000, weekly_pnl_pct=-0.04))
    assert "weekly_loss" in _types(PortfolioState(nav=1_000_000, weekly_pnl_pct=-0.0401))


def test_drawdown_threshold_boundary() -> None:
    assert "drawdown" not in _types(PortfolioState(nav=1_000_000, drawdown_pct=0.08))
    assert "drawdown" in _types(PortfolioState(nav=1_000_000, drawdown_pct=0.0801))


def test_single_position_threshold_boundary() -> None:
    assert "single_position" not in _types(PortfolioState(nav=1_000_000, max_position_pct=0.03))
    assert "single_position" in _types(PortfolioState(nav=1_000_000, max_position_pct=0.0301))


def test_circuit_breaker_persists_log(migrated_conn: sqlite3.Connection) -> None:
    events = fire_circuit_breakers(
        migrated_conn,
        state=PortfolioState(nav=1_000_000, daily_pnl_pct=-0.02),
    )
    assert events
    row = migrated_conn.execute("SELECT breaker_type FROM circuit_breaker_log").fetchone()
    assert row == ("daily_loss",)
