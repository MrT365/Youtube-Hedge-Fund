"""SCORE-09 sector-percentile rank utility.

Every factor module emits raw long-format values. This module turns those raw
values into 0-100 percentile ranks within GICS sector.

Locked decisions:
  - Average rank for ties.
  - NaN raw values are excluded and remain NaN.
  - N=0 -> all NaN; N=1 -> 50.0 neutral; N>=2 -> rank / N * 100.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata


def percentile_rank_within(values: np.ndarray) -> np.ndarray:
    """Return 0-100 percentile ranks for one cohort, preserving NaNs."""
    arr = np.asarray(values, dtype=float)
    out = np.full(arr.shape, np.nan, dtype=float)
    mask = ~np.isnan(arr)
    n = int(mask.sum())
    if n == 0:
        return out
    if n == 1:
        out[mask] = 50.0
        return out
    out[mask] = rankdata(arr[mask], method="average") / n * 100.0
    return out


def compute_sector_percentile_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``percentile_rank`` and ``n_in_sector`` using sector-level cohorts.

    Input must include ``sector`` and ``raw_value``. Other columns are passed
    through unchanged.
    """
    out = df.copy()
    out["percentile_rank"] = np.nan
    out["n_in_sector"] = 0
    for _, group in df.groupby("sector", dropna=False):
        ranks = percentile_rank_within(group["raw_value"].to_numpy(dtype=float))
        valid_n = int(group["raw_value"].notna().sum())
        out.loc[group.index, "percentile_rank"] = ranks
        out.loc[group.index, "n_in_sector"] = valid_n
    return out


__all__ = ["compute_sector_percentile_rank", "percentile_rank_within"]
