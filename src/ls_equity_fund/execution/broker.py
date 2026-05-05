"""IBKR broker adapter (EXEC-01 / EXEC-02).

Uses ``ib_async`` only. Live mode is blocked unless both operator gates are
present: ``MERIDIAN_LIVE_OK=1`` and the AUDIT-03 promotion record on disk.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import structlog
from ib_async import IB, LimitOrder, Stock

from ls_equity_fund.config import BrokerConfig
from ls_equity_fund.execution.base import Broker
from ls_equity_fund.schemas import Order, OrderId, Position, Side

log = structlog.get_logger(__name__)


class LiveTradingGateError(RuntimeError):
    """Raised when live trading is requested without both required gates."""


class MarketDataPermissionError(RuntimeError):
    """Raised when IBKR data is delayed or unavailable for execution."""


class IBKRBroker(Broker):
    """Thin ib_async-backed broker with paper/live separation."""

    def __init__(
        self,
        cfg: BrokerConfig,
        *,
        db_path: Path | None = None,
        ib: IB | None = None,
        connect: bool = True,
    ) -> None:
        if cfg.mode == "live":
            _assert_live_gates(cfg)
        self.cfg = cfg
        self._ib = ib or IB()
        self._db_path = db_path
        self._next_order_id = 1
        if connect:
            self.connect()

    @property
    def is_paper(self) -> bool:
        return self.cfg.mode == "paper"

    def connect(self) -> None:
        host = self.cfg.paper_host
        port = self.cfg.paper_port if self.is_paper else self.cfg.live_port
        self._ib.connect(host, port, clientId=self.cfg.client_id)
        self._next_order_id = max(self._load_local_order_id(), self._tws_next_valid_id())
        self._persist_local_order_id(self._next_order_id)

    def ensure_realtime_market_data(self) -> None:
        if not self.has_realtime_market_data():
            raise MarketDataPermissionError("IBKR market data is delayed or unavailable")

    def has_realtime_market_data(self) -> bool:
        market_data_type = getattr(self._ib, "marketDataType", None)
        return market_data_type not in (3, 4, "DELAYED", "DELAYED_FROZEN")

    def reconnect_after_drop(self) -> bool:
        for attempt in range(1, 4):
            log.warning("ibkr_session_drop_reconnect_pause", attempt=attempt, pause_seconds=60)
            time.sleep(60)
            try:
                self.connect()
                return True
            except Exception as exc:  # pragma: no cover - real gateway path
                log.warning("ibkr_reconnect_failed", attempt=attempt, error=str(exc))
        self.persist_state("halted_after_session_drop")
        return False

    def persist_state(self, reason: str) -> None:
        log.error("ibkr_execution_halted", reason=reason, next_order_id=self._next_order_id)
        self._persist_local_order_id(self._next_order_id)

    def place_order(self, order: Order) -> OrderId:
        self.ensure_realtime_market_data()
        side = "BUY" if order.side in (Side.BUY, Side.BUY_TO_COVER) else "SELL"
        contract = Stock(order.ticker, "SMART", "USD")
        ib_order = LimitOrder(side, order.qty, order.signal_price)
        trade = self._ib.placeOrder(contract, ib_order)
        broker_id = str(getattr(getattr(trade, "order", None), "orderId", self._next_order_id))
        self._next_order_id = max(self._next_order_id + 1, int(broker_id) + 1 if broker_id.isdigit() else self._next_order_id + 1)
        self._persist_local_order_id(self._next_order_id)
        return OrderId(broker_id)

    def get_order(self, order_id: OrderId) -> Order:
        raise KeyError(f"IBKR order lookup is status-stream based: {order_id}")

    def get_positions(self) -> list[Position]:
        out: list[Position] = []
        for p in self._ib.positions():
            contract = getattr(p, "contract", None)
            ticker = str(getattr(contract, "symbol", ""))
            qty = int(getattr(p, "position", 0))
            avg_cost = float(getattr(p, "avgCost", 0.0))
            if ticker and qty:
                out.append(Position(ticker=ticker, qty=qty, avg_cost=avg_cost))
        return out

    def cancel(self, order_id: OrderId) -> None:
        for trade in self._ib.openTrades():
            if str(getattr(getattr(trade, "order", None), "orderId", "")) == str(order_id):
                self._ib.cancelOrder(trade.order)
                return
        raise KeyError(f"unknown open IBKR order_id: {order_id}")

    def _tws_next_valid_id(self) -> int:
        client = getattr(self._ib, "client", None)
        get_req_id = getattr(client, "getReqId", None)
        if callable(get_req_id):
            return int(get_req_id())
        return 1

    def _load_local_order_id(self) -> int:
        if self._db_path is None or not self._db_path.exists():
            return 1
        with sqlite3.connect(str(self._db_path)) as conn:
            row = conn.execute("SELECT MAX(CAST(broker_order_id AS INTEGER)) FROM orders").fetchone()
        value = row[0] if row else None
        return int(value) + 1 if value is not None else 1

    def _persist_local_order_id(self, order_id: int) -> None:
        self._next_order_id = max(self._next_order_id, order_id)


def _assert_live_gates(cfg: BrokerConfig) -> None:
    if os.getenv("MERIDIAN_LIVE_OK") != "1":
        raise LiveTradingGateError("live mode requires MERIDIAN_LIVE_OK=1")
    promotion_path = Path(cfg.audit_promotion_path)
    if not promotion_path.exists():
        raise LiveTradingGateError("live mode requires AUDIT-03 promotion record")


__all__ = [
    "IBKRBroker",
    "LiveTradingGateError",
    "MarketDataPermissionError",
]
