"""Order lifecycle manager with clean SIGINT shutdown (EXEC-07)."""

from __future__ import annotations

import signal
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from ls_equity_fund.schemas import OrderId


@dataclass
class OrderManager:
    broker: Any
    conn: sqlite3.Connection
    pending_order_ids: set[OrderId] = field(default_factory=set)

    def track_pending(self, order_id: OrderId) -> None:
        self.pending_order_ids.add(order_id)

    def mark_terminal(self, order_id: OrderId) -> None:
        self.pending_order_ids.discard(order_id)

    def cancel_all_pending(self) -> None:
        for order_id in list(self.pending_order_ids):
            try:
                self.broker.cancel(order_id)
            finally:
                self.pending_order_ids.discard(order_id)

    def persist_shutdown_state(self, *, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO circuit_breaker_log (
                    timestamp, breaker_type, threshold, observed_value,
                    portfolio_state_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    "EXECUTION_SHUTDOWN",
                    0.0,
                    float(len(self.pending_order_ids)),
                    f'{{"reason": "{reason}", "positions_retained": true}}',
                ),
            )

    def handle_sigint(self, signum: int, frame: object | None) -> None:
        self.cancel_all_pending()
        self.persist_shutdown_state(reason=f"signal_{signum}")

    def install_sigint_handler(self) -> None:
        signal.signal(signal.SIGINT, self.handle_sigint)


__all__ = ["OrderManager"]
