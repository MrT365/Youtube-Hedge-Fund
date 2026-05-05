"""Sector-rank utility tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.sector_rank import (
    compute_sector_percentile_rank,
    percentile_rank_within,
)


def test_average_method() -> None:
    out = percentile_rank_within(np.array([10, 20, 30, 20]))
    assert out.tolist() == [25.0, 62.5, 100.0, 62.5]


def test_nan_excluded() -> None:
    out = percentile_rank_within(np.array([np.nan, 10, 20, 30]))
    assert np.isnan(out[0])
    assert out[1:] == pytest.approx([100 / 3, 200 / 3, 100.0])


def test_n1_neutral_50() -> None:
    assert percentile_rank_within(np.array([42.0])).tolist() == [50.0]


def test_n0_all_nan() -> None:
    out = percentile_rank_within(np.array([np.nan, np.nan]))
    assert np.isnan(out).all()


def test_ties_get_average_rank() -> None:
    out = percentile_rank_within(np.array([5, 5, 5, 5]))
    assert out.tolist() == [62.5, 62.5, 62.5, 62.5]


def test_compute_sector_percentile_rank_groups_by_sector() -> None:
    df = pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "D", "E"],
            "sector": ["Tech", "Tech", "Tech", "Health", "Health"],
            "raw_value": [10, 20, 30, 100, 200],
        }
    )
    out = compute_sector_percentile_rank(df)
    ranks = dict(zip(out["ticker"], out["percentile_rank"], strict=True))
    assert ranks["A"] == pytest.approx(100 / 3)
    assert ranks["B"] == pytest.approx(200 / 3)
    assert ranks["C"] == pytest.approx(100.0)
    assert ranks["D"] == pytest.approx(50.0)
    assert ranks["E"] == pytest.approx(100.0)


def test_n2_sector_50_and_100() -> None:
    df = pd.DataFrame({"ticker": ["A", "B"], "sector": ["Tech", "Tech"], "raw_value": [5, 10]})
    out = compute_sector_percentile_rank(df)
    assert out["percentile_rank"].tolist() == [50.0, 100.0]
