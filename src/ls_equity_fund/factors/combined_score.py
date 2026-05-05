"""Combined composite factor — equal-weighted rollup of the 8 base factors.

This module is the 9th registered factor. Unlike the base factors which read
raw market/fundamental data, ``compute_combined`` reads the persisted parent
scores from the 8 base factors for the same ``score_date`` and produces one
``raw_value`` per ticker — the simple mean of available base parent scores.

The orchestrator then runs the standard sector-percentile-rank pass over those
raw values, so the final ``combined`` row in ``factor_scores`` /
``factor_scores_parent`` is a 0-100 sector-relative composite.

Why register through the same ``@register_factor`` decorator?
  - Same return shape (``ticker``, ``sub_factor``, ``raw_value``)
  - Same downstream pipeline (sector_rank → write_factor_scores → parent agg
    → write_parent_scores)
  - Lets the CLI uniformly iterate the registry; combined just runs LAST after
    the base factors have persisted (handled by the orchestrator).
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import structlog

from ls_equity_fund.factors.composer import BASE_FACTORS, register_factor

log = structlog.get_logger(__name__)

SUB_FACTORS: tuple[str, ...] = ("combined",)


@register_factor("combined")
def compute_combined(
    conn: sqlite3.Connection,
    asof: date,
    tickers: list[str] | None,
) -> pd.DataFrame:
    """Read the 8 base parent scores for ``asof`` and emit equal-weighted means.

    Returns long-format rows (ticker, sub_factor='combined', raw_value=mean of
    base parent_scores). Missing base factors for a ticker reduce the
    denominator (mean over available factors, not over all 8). Tickers with no
    base parent_scores at all are skipped.

    The downstream sector-rank pass turns ``raw_value`` into a 0-100
    percentile_rank within sector — so two tickers with identical naive means
    can still differ in their final combined score depending on how their
    sector peers fared.
    """
    base_factors = list(BASE_FACTORS)
    placeholders = ",".join("?" * len(base_factors))
    score_date_str = asof.isoformat()

    query = (
        "SELECT ticker, factor, parent_score "
        "FROM factor_scores_parent "
        f"WHERE score_date = ? AND factor IN ({placeholders})"
    )
    params: list[object] = [score_date_str, *base_factors]
    if tickers:
        query += f" AND ticker IN ({','.join('?' * len(tickers))})"
        params.extend(tickers)

    parent_df = pd.read_sql_query(query, conn, params=params)

    if parent_df.empty:
        log.warning(
            "compute_combined_no_parents",
            asof=score_date_str,
            hint="run base factors before combined",
        )
        return _empty_result()

    # Average across the available base factors per ticker. .mean() on a Series
    # with NaN values would be 0-len after groupby drops NaNs, so we filter
    # explicitly. parent_score may be NULL for sectors with N=0 (rare).
    valid = parent_df.dropna(subset=["parent_score"])
    means = valid.groupby("ticker", as_index=False)["parent_score"].mean()
    means = means.rename(columns={"parent_score": "raw_value"})

    # Capture how many of the 8 base factors actually contributed — useful for
    # auditability + downstream consumers that may want to weight the composite
    # by completeness. Stored in factor_scores.n_in_sector by the standard
    # ranking pass; we surface it as raw_value's source via logging only.
    n_used = valid.groupby("ticker", as_index=False)["factor"].count()
    n_used = n_used.rename(columns={"factor": "n_factors_used"})
    log.info(
        "compute_combined_complete",
        asof=score_date_str,
        n_tickers=len(means),
        avg_factors_per_ticker=float(n_used["n_factors_used"].mean()) if not n_used.empty else 0.0,
    )

    out = means.assign(sub_factor="combined")
    out = out[["ticker", "sub_factor", "raw_value"]]
    out["raw_value"] = out["raw_value"].astype("float64")
    return out


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": pd.Series(dtype="object"),
            "sub_factor": pd.Series(dtype="object"),
            "raw_value": pd.Series(dtype="float64"),
        }
    )


__all__ = ["SUB_FACTORS", "compute_combined"]
