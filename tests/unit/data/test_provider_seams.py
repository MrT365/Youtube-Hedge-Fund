"""DATA-14 swap-in seam tests.

Verifies the six Phase 1 sibling provider ABCs are abstract, that the
``PolygonProvider`` stub instantiates against the union of all six (proving
the swap-in seam works), and that every Polygon method raises
``NotImplementedError`` with a "DATA-14" reference until the v1.x milestone.
"""
from __future__ import annotations

from datetime import date

import pytest

from ls_equity_fund.data.providers import (
    EstimatesProvider,
    FilingsProvider,
    FundamentalsProvider,
    MacroProvider,
    OHLCVProvider,
    PolygonProvider,
    ShortInterestProvider,
)


def test_six_sibling_abcs_declared() -> None:
    """Each sibling ABC declares at least one abstract method."""
    for cls in (
        OHLCVProvider,
        FundamentalsProvider,
        ShortInterestProvider,
        EstimatesProvider,
        FilingsProvider,
        MacroProvider,
    ):
        assert cls.__abstractmethods__, f"{cls.__name__} has no abstract methods"


def test_abcs_cannot_instantiate() -> None:
    """Sibling ABCs cannot be instantiated directly (D-22 / INFRA-03 contract)."""
    for cls in (
        OHLCVProvider,
        FundamentalsProvider,
        ShortInterestProvider,
        EstimatesProvider,
        FilingsProvider,
        MacroProvider,
    ):
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]


def test_polygon_stub_instantiates_validating_seam() -> None:
    """DATA-14 — Polygon stub instantiates without error (proves seam works)."""
    provider = PolygonProvider(api_key="dummy")
    assert isinstance(provider, OHLCVProvider)
    assert isinstance(provider, FundamentalsProvider)
    assert isinstance(provider, ShortInterestProvider)
    assert isinstance(provider, EstimatesProvider)
    assert isinstance(provider, FilingsProvider)
    assert isinstance(provider, MacroProvider)


def test_polygon_methods_raise_not_implemented() -> None:
    """Every Polygon method raises NotImplementedError with a DATA-14 reference."""
    provider = PolygonProvider()
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.get_prices(["AAPL"], date(2026, 1, 1), date(2026, 1, 31))
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.get_last_stored_date("AAPL")
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.get_fundamentals("AAPL")
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.get_short_interest("AAPL", date(2026, 1, 1))
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.get_estimates("AAPL", date(2026, 1, 1))
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.get_next_earnings_dates("AAPL")
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.fetch_filings("AAPL", ["10-K"])
    with pytest.raises(NotImplementedError, match="DATA-14"):
        provider.fetch_macro_events()


def test_polygon_provider_in_data_namespace() -> None:
    """``PolygonProvider`` is re-exported from ``ls_equity_fund.data``."""
    from ls_equity_fund.data import PolygonProvider as PP

    assert PP is PolygonProvider
