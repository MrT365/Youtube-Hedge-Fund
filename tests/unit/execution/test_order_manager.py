from __future__ import annotations

import sqlite3

from ls_equity_fund.execution.order_manager import OrderManager
from ls_equity_fund.schemas import OrderId


class PendingBroker:
    def __init__(self) -> None:
        self.cancelled: list[OrderId] = []

    def cancel(self, order_id: OrderId) -> None:
        self.cancelled.append(order_id)


def test_sigint_handler_cancels_pending_retains_positions_and_persists(
    conn: sqlite3.Connection,
) -> None:
    broker = PendingBroker()
    manager = OrderManager(broker=broker, conn=conn)
    manager.track_pending(OrderId("o1"))
    manager.track_pending(OrderId("o2"))

    manager.handle_sigint(2, None)

    assert set(broker.cancelled) == {OrderId("o1"), OrderId("o2")}
    assert manager.pending_order_ids == set()
    row = conn.execute(
        "SELECT breaker_type, portfolio_state_json FROM circuit_breaker_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "EXECUTION_SHUTDOWN"
    assert "positions_retained" in row[1]
