"""Tests for PaperBroker - deterministic-fill contract (D-06, D-07, D-08, D-10)."""
from __future__ import annotations

import pytest

from ls_equity_fund.execution.base import Broker
from ls_equity_fund.execution.paper_broker import PaperBroker
from ls_equity_fund.schemas import Order, OrderId, OrderStatus, Side


def _order(oid: str, ticker: str, side: Side, qty: int, price: float) -> Order:
    return Order(
        order_id=OrderId(oid),
        ticker=ticker,
        side=side,
        qty=qty,
        signal_price=price,
    )


def test_paper_broker_is_subclass_of_broker() -> None:
    """PaperBroker concretely implements Broker (instantiable, not abstract)."""
    assert issubclass(PaperBroker, Broker)
    PaperBroker()  # must not raise


def test_is_paper_true() -> None:
    """D-10: PaperBroker.is_paper is True."""
    assert PaperBroker().is_paper is True


def test_initial_state_is_empty() -> None:
    """Fresh broker has no orders and no positions."""
    b = PaperBroker()
    assert b.get_positions() == []


def test_place_order_fills_at_signal_price() -> None:
    """D-06: fill_price == signal_price exactly (zero slippage)."""
    b = PaperBroker()
    oid = b.place_order(_order("o1", "AAPL", Side.BUY, 10, 100.0))
    o = b.get_order(oid)
    assert o.status == OrderStatus.FILLED
    assert o.fill_price == 100.0
    assert o.fill_ts is not None


def test_place_order_returns_input_order_id() -> None:
    """PaperBroker preserves the caller's order_id; no broker-assigned reassignment."""
    b = PaperBroker()
    oid = b.place_order(_order("custom-id-42", "AAPL", Side.BUY, 1, 1.0))
    assert oid == OrderId("custom-id-42")


def test_place_order_full_fill_never_rejects() -> None:
    """D-07: always full fill, never reject (even at absurd qty)."""
    b = PaperBroker()
    b.place_order(_order("o1", "AAPL", Side.BUY, 1_000_000, 100.0))
    pos = next(p for p in b.get_positions() if p.ticker == "AAPL")
    assert pos.qty == 1_000_000


def test_buy_then_sell_round_trip_closes_flat() -> None:
    """Position math: BUY 10 -> SELL 10 closes flat (pruned from get_positions)."""
    b = PaperBroker()
    b.place_order(_order("o1", "AAPL", Side.BUY, 10, 100.0))
    b.place_order(_order("o2", "AAPL", Side.SELL, 10, 110.0))
    aapl = [p for p in b.get_positions() if p.ticker == "AAPL"]
    assert aapl == []


def test_short_then_cover_closes_flat() -> None:
    """SELL_SHORT 5 @ 50 then BUY_TO_COVER 5 @ 55 closes flat."""
    b = PaperBroker()
    b.place_order(_order("o1", "TSLA", Side.SELL_SHORT, 5, 50.0))
    pos = next(p for p in b.get_positions() if p.ticker == "TSLA")
    assert pos.qty == -5
    assert pos.avg_cost == 50.0
    b.place_order(_order("o2", "TSLA", Side.BUY_TO_COVER, 5, 55.0))
    assert all(p.ticker != "TSLA" for p in b.get_positions())


def test_same_side_avg_cost_weighted() -> None:
    """BUY 10 @ 100 then BUY 5 @ 110 -> qty=15, avg_cost=103.33..."""
    b = PaperBroker()
    b.place_order(_order("o1", "MSFT", Side.BUY, 10, 100.0))
    b.place_order(_order("o2", "MSFT", Side.BUY, 5, 110.0))
    pos = next(p for p in b.get_positions() if p.ticker == "MSFT")
    assert pos.qty == 15
    expected_avg = (10 * 100.0 + 5 * 110.0) / 15
    assert abs(pos.avg_cost - expected_avg) < 1e-9


def test_partial_reduction_keeps_avg_cost() -> None:
    """BUY 10 @ 100 then SELL 3 @ 120 -> qty=7, avg_cost still 100 (unrealized P&L logic is L7)."""
    b = PaperBroker()
    b.place_order(_order("o1", "NVDA", Side.BUY, 10, 100.0))
    b.place_order(_order("o2", "NVDA", Side.SELL, 3, 120.0))
    pos = next(p for p in b.get_positions() if p.ticker == "NVDA")
    assert pos.qty == 7
    assert pos.avg_cost == 100.0


def test_get_order_unknown_raises_keyerror() -> None:
    b = PaperBroker()
    with pytest.raises(KeyError):
        b.get_order(OrderId("does-not-exist"))


def test_duplicate_order_id_raises_value_error() -> None:
    """Operator bug guard: re-using an order_id is rejected."""
    b = PaperBroker()
    b.place_order(_order("o1", "AAPL", Side.BUY, 10, 100.0))
    with pytest.raises(ValueError, match="duplicate order_id"):
        b.place_order(_order("o1", "AAPL", Side.BUY, 5, 100.0))


def test_two_instances_have_independent_state() -> None:
    """D-08: in-memory state per instance; no shared SQLite/global."""
    b1 = PaperBroker()
    b2 = PaperBroker()
    b1.place_order(_order("o1", "AAPL", Side.BUY, 10, 100.0))
    assert b1.get_positions()
    assert b2.get_positions() == []


def test_cancel_filled_order_raises_value_error() -> None:
    """PaperBroker fills synchronously - cancelling a placed order is invalid."""
    b = PaperBroker()
    oid = b.place_order(_order("o1", "AAPL", Side.BUY, 10, 100.0))
    with pytest.raises(ValueError, match="PENDING"):
        b.cancel(oid)


def test_cancel_unknown_raises_keyerror() -> None:
    b = PaperBroker()
    with pytest.raises(KeyError):
        b.cancel(OrderId("nope"))


def test_signed_delta_helper() -> None:
    """Internal _signed_delta correctness (acceptance criterion)."""
    assert PaperBroker._signed_delta(Side.BUY, 10) == 10
    assert PaperBroker._signed_delta(Side.BUY_TO_COVER, 7) == 7
    assert PaperBroker._signed_delta(Side.SELL, 3) == -3
    assert PaperBroker._signed_delta(Side.SELL_SHORT, 5) == -5
