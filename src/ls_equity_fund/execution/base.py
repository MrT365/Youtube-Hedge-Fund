"""Broker seam (D-09, D-10, D-22, INFRA-03).

Phase 0 declares the MINIMAL surface (D-09): five methods + is_paper property.
Phase 8 will EXPAND with IBKR-specific methods (borrow check, ADV chunking,
slippage hooks). The minimal surface here is what's needed to run the L4->L5->L6
spine in tests against PaperBroker.

is_paper (D-10): non-negotiable. Phase 8's MERIDIAN_LIVE_OK gate keys off it -
a non-paper Broker must refuse to instantiate without the env var AND the
AUDIT-03 promotion record.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ls_equity_fund.schemas import Order, OrderId, Position


class Broker(ABC):
    """Abstract broker.

    Phase 0 surface (locked by D-09):
      - is_paper: bool property
      - place_order(order) -> OrderId
      - get_order(order_id) -> Order
      - get_positions() -> list[Position]
      - cancel(order_id) -> None

    Phase 8 will ADD methods (borrow check, fills streaming, etc).
    Do NOT add methods to this ABC in Phase 0.
    """

    @property
    @abstractmethod
    def is_paper(self) -> bool:
        """True for paper brokers; False for live brokers (D-10).

        The Phase 8 MERIDIAN_LIVE_OK gate refuses to instantiate any non-paper
        broker without both the env var and the AUDIT-03 promotion record.
        """

    @abstractmethod
    def place_order(self, order: Order) -> OrderId:
        """Submit an order. Returns the broker-assigned order_id.

        Implementations decide fill semantics:
          - PaperBroker: deterministic full fill at order.signal_price (D-06, D-07).
          - IBKRBroker (Phase 8): forwards to IBKR; status updates async.
        """

    @abstractmethod
    def get_order(self, order_id: OrderId) -> Order:
        """Look up an order by id. Raises KeyError if unknown."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return current open positions. Empty list if flat."""

    @abstractmethod
    def cancel(self, order_id: OrderId) -> None:
        """Cancel a PENDING order. Raises ValueError if not cancellable."""


__all__ = ["Broker"]
