"""Tests for the Phase 4 cost tracker (ANAL-02 + ANAL-03)."""

from __future__ import annotations

import pytest

from ls_equity_fund.analysis.cost_tracker import (
    CostCeilingExceeded,
    CostTracker,
    PriceTable,
)


def test_input_only_cost_math() -> None:
    # 1M input tokens × $3 = $3.00
    cost = CostTracker.cost_of(input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(3.00)


def test_output_billed_at_5x_input() -> None:
    cost = CostTracker.cost_of(input_tokens=0, output_tokens=1_000_000)
    assert cost == pytest.approx(15.00)


def test_cache_write_billed_at_1_25x_input() -> None:
    """ANAL-02 / CP2 — cache_creation costs 1.25× input rate, MUST be tracked."""
    cost = CostTracker.cost_of(
        input_tokens=0, output_tokens=0, cache_write_tokens=1_000_000
    )
    assert cost == pytest.approx(3.75)


def test_cache_read_billed_at_0_1x_input() -> None:
    cost = CostTracker.cost_of(
        input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000
    )
    assert cost == pytest.approx(0.30)


def test_record_accumulates_tokens_and_dollars() -> None:
    t = CostTracker(ceiling_usd=25.0)
    t.record({"input_tokens": 1000, "output_tokens": 200})
    t.record({"input_tokens": 500, "output_tokens": 50})

    assert t.input_tokens == 1500
    assert t.output_tokens == 250
    assert t.n_calls == 2
    # 1500 input × 3/1M + 250 output × 15/1M
    expected = 1500 * 3 / 1e6 + 250 * 15 / 1e6
    assert t.total_usd == pytest.approx(expected)


def test_record_handles_cache_fields() -> None:
    t = CostTracker()
    t.record(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 500,
        }
    )
    assert t.cache_write_tokens == 1000
    assert t.cache_read_tokens == 500


def test_record_handles_none_values() -> None:
    """Anthropic returns None when no cache activity — must not blow up."""
    t = CostTracker()
    t.record(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
        }
    )
    assert t.cache_write_tokens == 0
    assert t.cache_read_tokens == 0


def test_assert_under_ceiling_raises_at_threshold() -> None:
    """ANAL-03 — ceiling-hit aborts run."""
    t = CostTracker(ceiling_usd=0.001)  # tiny ceiling
    t.record({"input_tokens": 1000, "output_tokens": 0})  # = $0.003 > $0.001
    with pytest.raises(CostCeilingExceeded):
        t.assert_under_ceiling()


def test_assert_under_ceiling_passes_below() -> None:
    t = CostTracker(ceiling_usd=10.0)
    t.record({"input_tokens": 1000, "output_tokens": 200})
    t.assert_under_ceiling()  # should not raise


def test_would_exceed_pre_call_gate() -> None:
    t = CostTracker(ceiling_usd=1.00)
    t.record({"input_tokens": 200_000, "output_tokens": 0})  # = $0.60
    assert t.total_usd == pytest.approx(0.60)
    assert not t.would_exceed(0.10)  # 0.60 + 0.10 < 1.00
    assert t.would_exceed(0.50)  # 0.60 + 0.50 >= 1.00


def test_cache_hit_rate() -> None:
    t = CostTracker()
    t.record({"input_tokens": 1000, "cache_read_input_tokens": 4000})
    assert t.cache_hit_rate() == pytest.approx(0.8)


def test_cache_hit_rate_zero_when_no_inputs() -> None:
    t = CostTracker()
    assert t.cache_hit_rate() == 0.0


def test_summary_shape() -> None:
    t = CostTracker(ceiling_usd=25.0)
    t.record({"input_tokens": 1000, "output_tokens": 200})
    s = t.summary()
    assert set(s) >= {
        "calls",
        "input_tokens",
        "output_tokens",
        "cache_write_tokens",
        "cache_read_tokens",
        "cache_hit_rate",
        "total_usd",
        "ceiling_usd",
        "remaining_usd",
    }
    assert s["calls"] == 1
    assert s["remaining_usd"] == pytest.approx(25.0 - s["total_usd"])


def test_custom_price_table() -> None:
    cheap = PriceTable(input_per_mtok=1.0, output_per_mtok=2.0)
    cost = CostTracker.cost_of(
        input_tokens=1_000_000, output_tokens=1_000_000, prices=cheap
    )
    assert cost == pytest.approx(3.0)
