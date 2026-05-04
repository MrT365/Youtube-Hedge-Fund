"""PaperBroker - deterministic in-memory broker (Phase 0 stub).

Per CONTEXT D-06: fills at order.signal_price exactly (zero slippage).
Per CONTEXT D-07: always full fill, never reject (pre-trade veto in Phase 6 is the only rejector).
Per CONTEXT D-08: in-memory state only; no SQLite tables in Phase 0 for paper orders.
Per CONTEXT D-10: is_paper returns True; gates the live-mode check in Phase 8.

This stub lets the L4 -> L5 -> L6 spine be exercised in unit tests before any
IBKR connection exists. The slippage tracker (Phase 8 EXEC-04) will record 0 bps
against PaperBroker, which is the known/expected baseline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from ls_equity_fund.execution.base import Broker
from ls_equity_fund.schemas import Order, OrderId, OrderStatus, Position, Side


log = structlog.get_logger(__name__)


class PaperBroker(Broker):
    """Deterministic paper broker.

    Each instance maintains independent in-memory state:
      - _orders: dict[OrderId, Order]
      - _positions: dict[ticker, Position]   (qty signed; 0-qty entries pruned in get_positions)

    Concurrency: not thread-safe by design. Phase 0 callers are single-threaded.
    """

    def __init__(self) -> None:
        self._orders: dict[OrderId, Order] = {}
        self._positions: dict[str, Position] = {}

    @property
    def is_paper(self) -> bool:
        """Always True (D-10)."""
        return True

    def place_order(self, order: Order) -> OrderId:
        """Accept order; fill 100% at order.signal_price (D-06, D-07).

        Returns the order's own order_id (no broker-assigned id reassignment in Phase 0).
        Raises ValueError on duplicate order_id (operator bug guard).
        """
        if order.order_id in self._orders:
            raise ValueError(f"duplicate order_id: {order.order_id}")

        # Deterministic fill: copy the order with FILLED status and signal_price as fill_price.
        filled = order.model_copy(
            update={
                "status": OrderStatus.FILLED,
                "fill_price": order.signal_price,    # D-06: zero slippage
                "fill_ts": datetime.now(timezone.utc),
            }
        )
        self._orders[order.order_id] = filled
        self._apply_fill(filled)

        log.info(
            "paper_order_filled",
            order_id=str(order.order_id),
            ticker=order.ticker,
            side=order.side.value,
            qty=order.qty,
            fill_price=order.signal_price,
        )
        return order.order_id

    def get_order(self, order_id: OrderId) -> Order:
        """Look up an order. Raises KeyError if unknown."""
        if order_id not in self._orders:
            raise KeyError(f"unknown order_id: {order_id}")
        return self._orders[order_id]

    def get_positions(self) -> list[Position]:
        """Return current positions, qty=0 entries pruned."""
        return [p for p in self._positions.values() if p.qty != 0]

    def cancel(self, order_id: OrderId) -> None:
        """Cancel a PENDING order. Raises ValueError if not cancellable; KeyError if unknown.

        Note: PaperBroker fills synchronously, so orders are FILLED on return from
        place_order. cancel() is mostly an API-parity placeholder for the Phase 8
        IBKRBroker which has real PENDING states.
        """
        if order_id not in self._orders:
            raise KeyError(f"unknown order_id: {order_id}")
        order = self._orders[order_id]
        if order.status != OrderStatus.PENDING:
            raise ValueError(
                f"cannot cancel order in status {order.status}; only PENDING is cancellable"
            )
        cancelled = order.model_copy(update={"status": OrderStatus.CANCELLED})
        self._orders[order_id] = cancelled

    # ----- internal helpers -----

    def _apply_fill(self, filled: Order) -> None:
        """Mutate in-memory positions per fill side."""
        signed_qty = self._signed_delta(filled.side, filled.qty)
        existing = self._positions.get(filled.ticker)

        if existing is None:
            # New position. avg_cost = fill_price.
            self._positions[filled.ticker] = Position(
                ticker=filled.ticker,
                qty=signed_qty,
                avg_cost=filled.signal_price,
            )
            return

        new_qty = existing.qty + signed_qty
        if new_qty == 0:
            # Closed flat - keep zero-qty entry; get_positions prunes it.
            self._positions[filled.ticker] = Position(
                ticker=filled.ticker, qty=0, avg_cost=existing.avg_cost
            )
            return

        # Same-direction add: weighted avg_cost.
        same_direction = (
            (existing.qty > 0 and signed_qty > 0)
            or (existing.qty < 0 and signed_qty < 0)
        )
        if same_direction:
            existing_notional = abs(existing.qty) * existing.avg_cost
            fill_notional = abs(signed_qty) * filled.signal_price
            new_avg = (existing_notional + fill_notional) / abs(new_qty)
            self._positions[filled.ticker] = Position(
                ticker=filled.ticker, qty=new_qty, avg_cost=new_avg
            )
            return

        # Sign flip (long -> short or vice versa, with new_qty != 0):
        # avg_cost resets to the new fill price.
        if (existing.qty > 0) != (new_qty > 0) and new_qty != 0:
            self._positions[filled.ticker] = Position(
                ticker=filled.ticker, qty=new_qty, avg_cost=filled.signal_price
            )
            return

        # Partial reduction same side - keep existing avg_cost.
        self._positions[filled.ticker] = Position(
            ticker=filled.ticker, qty=new_qty, avg_cost=existing.avg_cost
        )

    @staticmethod
    def _signed_delta(side: Side, qty: int) -> int:
        """Return signed quantity delta for the given side."""
        if side in (Side.BUY, Side.BUY_TO_COVER):
            return qty
        if side in (Side.SELL, Side.SELL_SHORT):
            return -qty
        raise ValueError(f"unknown side: {side}")


__all__ = ["PaperBroker"]
