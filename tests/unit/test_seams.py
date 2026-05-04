"""Tests for the three swap-in seam ABCs and the package skeleton."""

from __future__ import annotations

import importlib

import pytest

from ls_equity_fund.data.base import MarketDataProvider
from ls_equity_fund.execution.base import Broker
from ls_equity_fund.portfolio.base import Optimizer


def test_market_data_provider_is_abstract() -> None:
    """MarketDataProvider cannot be instantiated directly (D-22, INFRA-03)."""
    with pytest.raises(TypeError, match="abstract"):
        MarketDataProvider()  # type: ignore[abstract]


def test_optimizer_is_abstract() -> None:
    """Optimizer cannot be instantiated directly (D-22, INFRA-03)."""
    with pytest.raises(TypeError, match="abstract"):
        Optimizer()  # type: ignore[abstract]


def test_broker_is_abstract() -> None:
    """Broker cannot be instantiated directly (D-09, D-22)."""
    with pytest.raises(TypeError, match="abstract"):
        Broker()  # type: ignore[abstract]


def test_all_layer_packages_importable() -> None:
    """Eight package directories per CONTEXT D-22."""
    for layer in (
        "data",
        "factors",
        "analysis",
        "portfolio",
        "risk",
        "execution",
        "reporting",
        "dashboard",
    ):
        mod = importlib.import_module(f"ls_equity_fund.{layer}")
        assert mod is not None, f"layer {layer!r} failed to import"


def test_broker_abc_surface_locked() -> None:
    """D-09: Broker ABC declares EXACTLY 5 abstract members + is_paper property.

    Phase 8 will EXPAND this surface - but Phase 0 must not.
    Adding a method requires updating this test, which forces a planning
    conversation (T-00-19 mitigation).
    """
    abstract_names = set(Broker.__abstractmethods__)
    expected = {"is_paper", "place_order", "get_order", "get_positions", "cancel"}
    assert abstract_names == expected, (
        f"Broker ABC surface drifted from D-09. "
        f"Expected {expected}, got {abstract_names}. "
        f"Adding methods is a Phase 8 task."
    )


def test_market_data_provider_minimal_surface() -> None:
    """MarketDataProvider declares 4 methods (Phase 0 minimal)."""
    abstract_names = set(MarketDataProvider.__abstractmethods__)
    assert abstract_names == {
        "get_prices",
        "get_fundamentals",
        "get_short_interest",
        "get_estimates",
    }


def test_optimizer_minimal_surface() -> None:
    """Optimizer declares one method: optimize."""
    abstract_names = set(Optimizer.__abstractmethods__)
    assert abstract_names == {"optimize"}


def test_schemas_exports() -> None:
    """schemas.py exports the locked Phase 0 set."""
    from ls_equity_fund.schemas import (
        Order,
        OrderId,  # noqa: F401
        OrderStatus,
        Position,
        Side,
    )

    assert Order is not None
    assert Position is not None
    assert Side.BUY.value == "BUY"
    assert Side.SELL_SHORT.value == "SELL_SHORT"
    assert Side.BUY_TO_COVER.value == "BUY_TO_COVER"
    assert Side.SELL.value == "SELL"
    assert OrderStatus.FILLED.value == "FILLED"
    assert OrderStatus.PENDING.value == "PENDING"
    assert OrderStatus.CANCELLED.value == "CANCELLED"


def test_seam_abcs_use_documented_module_paths() -> None:
    """The three seam ABCs live at their D-22 module paths.

    Concrete implementations are siblings (e.g. data/providers/yfinance_provider.py
    in Phase 1). The base.py convention is locked here.
    """
    assert MarketDataProvider.__module__ == "ls_equity_fund.data.base"
    assert Optimizer.__module__ == "ls_equity_fund.portfolio.base"
    assert Broker.__module__ == "ls_equity_fund.execution.base"
