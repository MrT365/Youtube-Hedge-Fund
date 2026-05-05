"""Composer registry and parent-score tests."""

from __future__ import annotations

import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.factors.composer import (
    FACTOR_REGISTRY,
    compute_parent_factor_score,
    register_factor,
)


def test_factor_registry_has_8_keys() -> None:
    assert sorted(FACTOR_REGISTRY.keys()) == [
        "growth",
        "insider",
        "institutional",
        "momentum",
        "quality",
        "revisions",
        "short_interest",
        "value",
    ]


def test_register_factor_decorator_overwrites_placeholder() -> None:
    original = FACTOR_REGISTRY["value"]

    @register_factor("value")
    def my_fn(conn: sqlite3.Connection, asof: date, tickers: list[str] | None) -> pd.DataFrame:
        return pd.DataFrame()

    try:
        assert FACTOR_REGISTRY["value"] is my_fn
    finally:
        FACTOR_REGISTRY["value"] = original


def test_register_factor_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        register_factor("bogus")


def test_compute_parent_factor_score_equal_weighted_mean() -> None:
    df = pd.DataFrame(
        {
            "ticker": ["A", "A", "B", "B"],
            "score_date": ["2026-05-04"] * 4,
            "factor": ["momentum"] * 4,
            "sub_factor": ["x", "y", "x", "y"],
            "percentile_rank": [60.0, 80.0, np.nan, 90.0],
            "sector": ["Tech"] * 4,
        }
    )
    out = compute_parent_factor_score(df)
    scores = dict(zip(out["ticker"], out["parent_score"], strict=True))
    used = dict(zip(out["ticker"], out["n_subfactors_used"], strict=True))
    assert scores["A"] == 70.0
    assert scores["B"] == 90.0
    assert used["A"] == 2
    assert used["B"] == 1


def test_compute_parent_factor_score_all_nan_yields_nan() -> None:
    df = pd.DataFrame(
        {
            "ticker": ["A", "A"],
            "score_date": ["2026-05-04", "2026-05-04"],
            "factor": ["momentum", "momentum"],
            "sub_factor": ["x", "y"],
            "percentile_rank": [np.nan, np.nan],
            "sector": ["Tech", "Tech"],
        }
    )
    out = compute_parent_factor_score(df)
    assert np.isnan(out["parent_score"].iloc[0])
    assert out["n_subfactors_used"].iloc[0] == 0
