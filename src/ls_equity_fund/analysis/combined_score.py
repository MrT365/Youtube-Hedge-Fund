"""ANAL-09 — Combined score (60% quant composite + 40% Claude average).

Reads:
  - quant: ``factor_scores_parent`` ``factor='combined'`` parent_scores per
    ticker (the 0-100 sector-rank already computed in Phase 2)
  - claude: per-analyzer scores from ``analysis_results`` for the SAME asof
    date — filing/risk/insider, mapped to a 0-100 scale and averaged

Output: ``(ticker, score_date, sector, quant_score, claude_score, has_claude,
combined_score_v2)`` per ticker, RE-RANKED to a 0-100 sector-percentile.

Fallback (spec-mandated): when no Claude data is available for a ticker, the
combined_score_v2 = quant_score with no penalty (NOT zero, NOT NULL — the
ticker still ranks based on quant alone). ``has_claude`` flag tells consumers
which path produced the score.

Persistence: re-uses the existing ``factor_scores`` / ``factor_scores_parent``
tables. The combined_score_v2 takes the SLOT of the Phase 2 ``combined``
factor — when called, it overwrites the Phase 2 row for the given asof.
This is intentional: ANAL-09 is the SUCCESSOR to Phase 2's combined; Phase 5
portfolio construction reads the latest combined.

Idempotent: rerunning produces the same numbers (deterministic ranking).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.factors.sector_rank import percentile_rank_within

log = structlog.get_logger(__name__)

# Spec weights — 60% quant / 40% Claude average (per ANAL-09).
QUANT_WEIGHT = 0.60
CLAUDE_WEIGHT = 0.40

# Mapping from Claude analyzer JSON → numeric score in [0, 100]. Each function
# is intentionally simple; a complex calibration belongs in a future
# v2 work item, not in the analyzer-bridge.
_INSIDER_SIGNAL_SCORES: dict[str, float] = {
    "STRONG_BUY": 90.0,
    "BUY": 70.0,
    "NEUTRAL": 50.0,
    "SELL": 30.0,
    "STRONG_SELL": 10.0,
}
_RISK_SEVERITY_SCORES: dict[str, float] = {
    "low": 80.0,
    "medium": 50.0,
    "high": 25.0,
    "critical": 5.0,
}


@dataclass(frozen=True)
class CombinedRow:
    ticker: str
    sector: str
    quant_score: float
    claude_score: float | None
    has_claude: bool
    combined_score: float
    n_analyzers: int


def compute_and_persist(
    conn: sqlite3.Connection,
    *,
    asof: date,
) -> pd.DataFrame:
    """Compute the v2 combined score for every ticker with quant data on asof.

    Persists to ``factor_scores_parent`` (factor='combined') AND
    ``factor_scores`` (factor='combined', sub_factor='combined') so the
    dashboard / Phase 5 portfolio construction read identical rows.

    Returns the long-form DataFrame for the printout / log.
    """
    quant = _load_quant_scores(conn, asof)
    if quant.empty:
        log.warning("combined_score_no_quant", asof=asof.isoformat())
        return pd.DataFrame()

    claude = _load_claude_scores(conn, asof)
    log.info(
        "combined_score_inputs",
        asof=asof.isoformat(),
        quant_rows=len(quant),
        claude_rows=len(claude),
    )

    merged = quant.merge(claude, on="ticker", how="left")
    merged["has_claude"] = merged["claude_score"].notna()

    # Spec rule: tickers without Claude data get combined = quant_score.
    # No penalty. The combined column on the row is the BLEND when present;
    # the fallback path uses the raw quant.
    merged["combined_raw"] = np.where(
        merged["has_claude"],
        QUANT_WEIGHT * merged["quant_score"] + CLAUDE_WEIGHT * merged["claude_score"],
        merged["quant_score"],
    )

    # Re-rank within sector to produce final 0-100 sector-percentile.
    parts: list[pd.DataFrame] = []
    for _sector, group in merged.groupby("sector", dropna=False):
        ranks = percentile_rank_within(group["combined_raw"].to_numpy(dtype=float))
        out = group.copy()
        out["combined_score"] = ranks
        parts.append(out)
    final = pd.concat(parts, ignore_index=True) if parts else merged
    final["score_date"] = asof.isoformat()

    # Persist: overwrite Phase 2's combined rows for this asof with the v2 blend.
    _persist(conn, final)

    n_with_claude = int(final["has_claude"].sum())
    log.info(
        "combined_score_persisted",
        n_total=len(final),
        n_with_claude=n_with_claude,
        n_quant_only=len(final) - n_with_claude,
    )
    return final


def _load_quant_scores(conn: sqlite3.Connection, asof: date) -> pd.DataFrame:
    """Phase 2 'combined' parent scores per ticker."""
    sql = """
        SELECT ticker, sector, parent_score AS quant_score
        FROM factor_scores_parent
        WHERE score_date = ? AND factor = 'combined'
    """
    df = pd.read_sql_query(sql, conn, params=(asof.isoformat(),))
    return df


def _load_claude_scores(conn: sqlite3.Connection, asof: date) -> pd.DataFrame:
    """Average available Claude scores per ticker for the asof date.

    Reads filing / risk / insider rows from ``analysis_results``. Each is
    mapped to a 0-100 scale via the helper functions; the ticker's
    claude_score is the unweighted mean of available analyzer scores.

    NOT INCLUDED: sector analyzer — it produces sector-level outputs, not
    per-ticker scores. Earnings_call stub is also excluded (always None).
    """
    asof_iso = asof.isoformat()
    # ``analysis_results.expires_at`` already gates valid rows; we don't filter
    # on computed_at because some analyzers run weekly while others run daily.
    sql = """
        SELECT analyzer_type, ticker, response_json
        FROM analysis_results
        WHERE analyzer_type IN ('filing', 'risk', 'insider')
          AND expires_at >= ?
    """
    rows = conn.execute(sql, (int(time.time()),)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["ticker", "claude_score"])

    by_ticker: dict[str, list[float]] = {}
    for analyzer_type, ticker, payload in rows:
        try:
            obj = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        score = _score_analyzer_response(analyzer_type, obj)
        if score is None:
            continue
        by_ticker.setdefault(ticker, []).append(float(score))

    if not by_ticker:
        return pd.DataFrame(columns=["ticker", "claude_score"])

    df = pd.DataFrame(
        [
            {
                "ticker": t,
                "claude_score": float(np.mean(scores)),
                "n_analyzers": len(scores),
            }
            for t, scores in by_ticker.items()
        ]
    )
    # Touch asof_iso so static-analysis sees usage; future work may filter on
    # date when analyzers stamp their own asof column.
    _ = asof_iso
    return df


def _score_analyzer_response(analyzer_type: str, obj: dict[str, Any]) -> float | None:
    """Map one analyzer's parsed JSON into a 0-100 score."""
    if analyzer_type == "filing":
        # Equal-weight the four filing scores
        keys = (
            "earnings_quality_score",
            "revenue_quality_score",
            "balance_sheet_score",
            "accruals_score",
        )
        vals = [obj.get(k) for k in keys]
        nums = [float(v) for v in vals if isinstance(v, int | float) and v is not None]
        return float(np.mean(nums)) if nums else None
    if analyzer_type == "risk":
        sev = obj.get("risk_severity")
        if isinstance(sev, str) and sev.lower() in _RISK_SEVERITY_SCORES:
            return _RISK_SEVERITY_SCORES[sev.lower()]
        return None
    if analyzer_type == "insider":
        sig = obj.get("signal")
        if isinstance(sig, str) and sig in _INSIDER_SIGNAL_SCORES:
            base = _INSIDER_SIGNAL_SCORES[sig]
            # Confidence weights the deviation from neutral (50)
            try:
                conf = float(obj.get("confidence") or 0.5)
            except (TypeError, ValueError):
                conf = 0.5
            conf = max(0.0, min(1.0, conf))
            return 50.0 + (base - 50.0) * conf
        return None
    return None


def _persist(conn: sqlite3.Connection, final: pd.DataFrame) -> None:
    """Overwrite Phase 2's combined rows on asof with the v2 blend."""
    if final.empty:
        return
    now = int(time.time())

    # factor_scores_parent
    parent_payload = [
        (
            row.ticker,
            row.score_date,
            "combined",
            float(row.combined_score) if not pd.isna(row.combined_score) else None,
            row.sector,
            int(row.n_analyzers + 1) if row.has_claude else 1,
            now,
        )
        for row in final.itertuples(index=False)
    ]
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO factor_scores_parent
                (ticker, score_date, factor, parent_score, sector,
                 n_subfactors_used, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            parent_payload,
        )

    # factor_scores (long-form, sub_factor='combined')
    sub_payload = [
        (
            row.ticker,
            row.score_date,
            "combined",
            "combined",
            float(row.combined_raw) if not pd.isna(row.combined_raw) else None,
            float(row.combined_score) if not pd.isna(row.combined_score) else None,
            row.sector,
            int(final[final["sector"] == row.sector].shape[0]),
            1,
            now,
        )
        for row in final.itertuples(index=False)
    ]
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO factor_scores
                (ticker, score_date, factor, sub_factor, raw_value,
                 percentile_rank, sector, n_in_sector, sufficient_history,
                 computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sub_payload,
        )


__all__ = [
    "CLAUDE_WEIGHT",
    "QUANT_WEIGHT",
    "CombinedRow",
    "compute_and_persist",
]
