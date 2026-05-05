"""ANAL-08 — Sector analyzer.

Reads top-N candidates within ONE GICS sector (sourced from
``factor_scores_parent`` for the asof date) and returns top long/short ideas
plus a sector outlook. One Claude call per sector.

Cache key: ``(sector, score_date)``. The artifact_id is the sector name, the
ticker column on ``analysis_results`` is set to a sector sentinel
``__sector__`` so the row remains uniquely keyed without conflicting with
ticker-level analyzer rows.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

import pandas as pd
import structlog

from ls_equity_fund.analysis import cache as analysis_cache
from ls_equity_fund.analysis.claude_client import (
    ClaudeClient,
    estimate_cost,
    load_prompt,
    parse_json,
)

log = structlog.get_logger(__name__)

ANALYZER_TYPE = "sector"
SECTOR_SENTINEL_TICKER = "__sector__"
DEFAULT_TOP_PER_SECTOR = 15

VALID_STANCES = ("bullish", "neutral", "bearish")


def analyze(
    *,
    conn: sqlite3.Connection,
    client: ClaudeClient,
    sector: str,
    asof: date,
    top_per_sector: int = DEFAULT_TOP_PER_SECTOR,
    run_id: str | None = None,
    use_cache: bool = True,
    ttl_days: int = analysis_cache.DEFAULT_TTL_DAYS,
) -> dict[str, Any] | None:
    """Run sector analyzer for one sector; return parsed JSON or None.

    None if the sector has no candidates with parent_scores on asof.
    """
    log = structlog.get_logger(__name__).bind(sector=sector, analyzer=ANALYZER_TYPE)

    artifact_id = f"sector-{sector}-{asof.isoformat()}"

    if use_cache:
        hit = analysis_cache.get(
            conn,
            analyzer_type=ANALYZER_TYPE,
            ticker=SECTOR_SENTINEL_TICKER,
            artifact_id=artifact_id,
        )
        if hit is not None:
            log.info("cache_hit", artifact_id=artifact_id)
            return hit.response

    candidates = _load_sector_candidates(conn, sector, asof, top_per_sector)
    if candidates.empty:
        log.warning("no_sector_candidates")
        return None

    user_message = _build_user_message(sector, asof, candidates)
    system_blocks = [load_prompt("sector")]

    response = client.call(
        system_blocks=system_blocks,
        user_message=user_message,
        max_tokens=1500,
    )
    try:
        parsed = parse_json(response.text)
    except ValueError as exc:
        log.warning("parse_failed", error=str(exc))
        return None

    parsed = _validate_sector_response(parsed, sector)

    if use_cache:
        cost_usd = client.cost_tracker.cost_of(
            input_tokens=response.usage.get("input_tokens", 0),
            output_tokens=response.usage.get("output_tokens", 0),
            cache_write_tokens=response.usage.get("cache_creation_input_tokens", 0),
            cache_read_tokens=response.usage.get("cache_read_input_tokens", 0),
            prices=client.cost_tracker.prices,
        )
        analysis_cache.put(
            conn,
            analyzer_type=ANALYZER_TYPE,
            ticker=SECTOR_SENTINEL_TICKER,
            artifact_id=artifact_id,
            run_id=run_id,
            model=client.model,
            response=parsed,
            input_tokens=response.usage.get("input_tokens", 0),
            output_tokens=response.usage.get("output_tokens", 0),
            cache_read_tokens=response.usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=response.usage.get("cache_creation_input_tokens", 0),
            cost_usd=cost_usd,
            ttl_days=ttl_days,
        )
    return parsed


def estimate_run_cost(n_sectors: int) -> float:
    if n_sectors <= 0:
        return 0.0
    sys_chars = 4500
    user_chars = 3500
    out_chars = 1500
    first = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=0)
    from ls_equity_fund.analysis.cost_tracker import CostTracker

    write_cost = CostTracker.cost_of(
        input_tokens=0, output_tokens=0, cache_write_tokens=sys_chars // 4
    )
    first += write_cost
    rest = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=sys_chars)
    return first + (n_sectors - 1) * rest


# --- helpers ----------------------------------------------------------------


def _load_sector_candidates(
    conn: sqlite3.Connection, sector: str, asof: date, top: int
) -> pd.DataFrame:
    """Top-N candidates in sector with all parent factor scores wide-pivoted."""
    sql = """
        SELECT ticker, factor, parent_score
        FROM factor_scores_parent
        WHERE sector = ? AND score_date = ?
    """
    long_df = pd.read_sql_query(sql, conn, params=(sector, asof.isoformat()))
    if long_df.empty:
        return long_df

    wide = long_df.pivot_table(
        index="ticker", columns="factor", values="parent_score", aggfunc="first"
    )
    if "combined" not in wide.columns:
        return pd.DataFrame()

    wide = wide.sort_values("combined", ascending=False).head(top).reset_index()
    return wide


def _build_user_message(sector: str, asof: date, candidates: pd.DataFrame) -> str:
    parts = [
        f"Sector: {sector}",
        f"As-of date: {asof.isoformat()}",
        f"Candidates supplied: {len(candidates)}",
        "",
        "Per-candidate parent factor scores (0-100 sector-percentile rank, "
        "ordered by combined score):",
        candidates.to_markdown(index=False, floatfmt=",.1f"),
        "",
        "Apply the rubric. Return only the JSON object specified in your instructions.",
    ]
    return "\n".join(parts)


def _validate_sector_response(obj: dict[str, Any], sector: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["sector"] = str(obj.get("sector") or sector)[:120]
    out["top_long_idea"] = _coerce_idea(obj.get("top_long_idea"))
    out["top_short_idea"] = _coerce_idea(obj.get("top_short_idea"))
    out["sector_outlook"] = str(obj.get("sector_outlook") or "")[:1500]
    stance = obj.get("outlook_stance")
    out["outlook_stance"] = (
        stance if isinstance(stance, str) and stance.lower() in VALID_STANCES else "neutral"
    )
    out["one_line_summary"] = str(obj.get("one_line_summary") or "")[:160]
    return out


def _coerce_idea(idea: Any) -> dict[str, Any]:
    if not isinstance(idea, dict):
        return {"ticker": "", "thesis": "", "key_drivers": [], "risk_to_thesis": ""}
    return {
        "ticker": str(idea.get("ticker") or "")[:8],
        "thesis": str(idea.get("thesis") or "")[:1000],
        "key_drivers": list(idea.get("key_drivers") or [])[:8],
        "risk_to_thesis": str(idea.get("risk_to_thesis") or "")[:300],
    }


__all__ = [
    "ANALYZER_TYPE",
    "DEFAULT_TOP_PER_SECTOR",
    "SECTOR_SENTINEL_TICKER",
    "VALID_STANCES",
    "analyze",
    "estimate_run_cost",
]
