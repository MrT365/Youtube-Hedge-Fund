"""SCORE-09 parent-score composition and factor registry dispatch."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

FACTOR_NAMES: tuple[str, ...] = (
    "momentum",
    "value",
    "quality",
    "growth",
    "revisions",
    "short_interest",
    "insider",
    "institutional",
)

FactorComputeFn = Callable[[sqlite3.Connection, date, list[str] | None], pd.DataFrame]


def _placeholder(name: str) -> FactorComputeFn:
    def _fn(conn: sqlite3.Connection, asof: date, tickers: list[str] | None) -> pd.DataFrame:
        raise NotImplementedError(f"factor {name!r} not yet registered")

    return _fn


FACTOR_REGISTRY: dict[str, FactorComputeFn] = {name: _placeholder(name) for name in FACTOR_NAMES}


def register_factor(name: str) -> Callable[[FactorComputeFn], FactorComputeFn]:
    """Register a factor compute function under one of the eight canonical names."""
    if name not in FACTOR_NAMES:
        raise ValueError(f"unknown factor name {name!r}; expected one of {FACTOR_NAMES}")

    def decorator(fn: FactorComputeFn) -> FactorComputeFn:
        FACTOR_REGISTRY[name] = fn
        log.debug("factor_registered", name=name, fn=fn.__qualname__)
        return fn

    return decorator


def compute_parent_factor_score(subfactors_df: pd.DataFrame) -> pd.DataFrame:
    """Compute equal-weighted parent scores from sub-factor percentile ranks."""
    cols = ["ticker", "score_date", "factor", "parent_score", "sector", "n_subfactors_used"]
    if subfactors_df.empty:
        return pd.DataFrame(columns=cols)
    grouped = subfactors_df.groupby(
        ["ticker", "score_date", "factor", "sector"],
        dropna=False,
        as_index=False,
    ).agg(
        parent_score=("percentile_rank", "mean"),
        n_subfactors_used=("percentile_rank", lambda s: int(s.notna().sum())),
    )
    return grouped[cols]


__all__ = [
    "FACTOR_NAMES",
    "FACTOR_REGISTRY",
    "FactorComputeFn",
    "compute_parent_factor_score",
    "register_factor",
]
