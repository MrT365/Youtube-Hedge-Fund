"""Shared Pydantic models for cross-layer control objects.

Per ARCHITECTURE.md §5: Pydantic models for control / config; pandas DataFrames for bulk data.
Per CONTEXT D-09: Phase 0 ships a minimal Order/Position/OrderId surface.
                  Phase 8 will EXPAND with broker_order_id, fills[], slippage_bps, etc.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import NewType

from pydantic import BaseModel, ConfigDict, Field

# Phase 0: order_id is a string. Phase 8 may switch to UUID-typed if useful.
OrderId = NewType("OrderId", str)


class Side(StrEnum):
    """Trade side. Closing-trade definition (Phase 6 RISK-04) operates on positions, not Side."""

    BUY = "BUY"
    SELL = "SELL"
    BUY_TO_COVER = "BUY_TO_COVER"
    SELL_SHORT = "SELL_SHORT"


class OrderStatus(StrEnum):
    """Order lifecycle status. Phase 0 minimal set; Phase 8 expands."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class Order(BaseModel):
    """Phase 0 minimal Order. Phase 8 expands (broker_order_id, TIF, limit_price, etc)."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    order_id: OrderId
    ticker: str
    side: Side
    qty: int = Field(gt=0)  # always positive; side determines direction
    signal_price: float = Field(gt=0)
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    fill_ts: datetime | None = None


class Position(BaseModel):
    """In-memory position (Phase 0 PaperBroker). Phase 5 PortfolioState DB rows are richer."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    ticker: str
    qty: int  # signed: + long, - short, 0 = no position (typically pruned)
    avg_cost: float


__all__ = ["Order", "OrderId", "OrderStatus", "Position", "Side"]
