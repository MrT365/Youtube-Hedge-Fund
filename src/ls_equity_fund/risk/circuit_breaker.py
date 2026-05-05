"""Circuit breakers (RISK-06, RISK-07)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PortfolioState:
    nav: float
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    drawdown_pct: float = 0.0
    max_position_pct: float = 0.0


@dataclass(frozen=True)
class CircuitBreakerEvent:
    breaker_type: str
    threshold: float
    observed_value: float
    action: str


def evaluate_circuit_breakers(state: PortfolioState) -> list[CircuitBreakerEvent]:
    """Return all breaker firings for the current portfolio state."""
    events: list[CircuitBreakerEvent] = []
    if state.daily_pnl_pct < -0.025:
        events.append(CircuitBreakerEvent("daily_loss_hard", -0.025, state.daily_pnl_pct, "CLOSE_ALL_TODAY"))
    elif state.daily_pnl_pct < -0.015:
        events.append(CircuitBreakerEvent("daily_loss", -0.015, state.daily_pnl_pct, "SIZE_DOWN_30"))

    if state.weekly_pnl_pct < -0.04:
        events.append(CircuitBreakerEvent("weekly_loss", -0.04, state.weekly_pnl_pct, "SIZE_DOWN_30"))
    if state.drawdown_pct > 0.08:
        events.append(CircuitBreakerEvent("drawdown", 0.08, state.drawdown_pct, "KILL_SWITCH"))
    if state.max_position_pct > 0.03:
        events.append(
            CircuitBreakerEvent(
                "single_position",
                0.03,
                state.max_position_pct,
                "FORCE_CLOSE_POSITION",
            )
        )
    return events


def fire_circuit_breakers(
    conn: sqlite3.Connection,
    *,
    state: PortfolioState,
    persist: bool = True,
) -> list[CircuitBreakerEvent]:
    """Evaluate and optionally persist all breaker firings."""
    events = evaluate_circuit_breakers(state)
    if persist:
        for event in events:
            write_circuit_breaker_log(conn, event=event, portfolio_state=asdict(state))
    return events


def write_circuit_breaker_log(
    conn: sqlite3.Connection,
    *,
    event: CircuitBreakerEvent,
    portfolio_state: dict[str, Any],
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO circuit_breaker_log (
                timestamp, breaker_type, threshold, observed_value, portfolio_state_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                event.breaker_type,
                event.threshold,
                event.observed_value,
                json.dumps(
                    {
                        **portfolio_state,
                        "action": event.action,
                    },
                    sort_keys=True,
                    default=str,
                ),
            ),
        )


def load_recent_circuit_breakers(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT timestamp, breaker_type, threshold, observed_value, portfolio_state_json
        FROM circuit_breaker_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "timestamp": row[0],
            "breaker_type": row[1],
            "threshold": row[2],
            "observed_value": row[3],
            "portfolio_state": json.loads(row[4]),
        }
        for row in rows
    ]


__all__ = [
    "CircuitBreakerEvent",
    "PortfolioState",
    "evaluate_circuit_breakers",
    "fire_circuit_breakers",
    "load_recent_circuit_breakers",
    "write_circuit_breaker_log",
]
