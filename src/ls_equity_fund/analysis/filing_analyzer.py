"""ANAL-05 — Filing analyzer.

Pulls 8 quarters of fundamentals from Phase 1's ``fundamentals`` +
``fundamental_ratios`` tables, packages them into a compact tabular brief,
and asks Claude to score earnings quality / revenue quality / balance-sheet
health / accruals plus surface red/green flags and a risk_level.

Caching:
  - artifact_id = "8q-{period_end}" so re-running the same date is free.
  - The system block (frozen v1 prompt) is cache_control'd; user message
    contains the per-ticker tabular data — variable, no cache.

Cost target: ~3-6K input chars per ticker, ~700 output chars → <$0.05 fresh,
<$0.005 cached. 40 tickers × $0.05 = $2.00 well under $25 ceiling.
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

ANALYZER_TYPE = "filing"
LOOKBACK_QUARTERS = 8

# Field set we lift into the brief. Skipping ratios with high NaN incidence
# (FCF yield needs market_cap, often NULL in Phase 1 free-feed mode).
# Fundamentals + ratios use WIDE columns per Phase 1 schema. We pick a
# focused subset to keep the prompt compact (~3K chars per ticker).
_FUND_COLUMNS = (
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "cfo",
    "free_cash_flow",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "shares_outstanding",
    "accruals",
    "working_capital",
)
_RATIO_COLUMNS = (
    "roe",
    "roa",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "earnings_growth_yoy",
    "debt_to_equity",
    "current_ratio",
    "cfo_to_ni",
    "accruals_ratio",
)


def analyze(
    *,
    conn: sqlite3.Connection,
    client: ClaudeClient,
    ticker: str,
    asof: date,
    run_id: str | None = None,
    use_cache: bool = True,
    ttl_days: int = analysis_cache.DEFAULT_TTL_DAYS,
) -> dict[str, Any] | None:
    """Run the filing analyzer for one ticker; return parsed JSON or None.

    Returns None if the ticker has no fundamentals in the DB at all
    (degenerate case — Phase 5 falls back to 100% quant when Claude data
    is absent, per ANAL-09).
    """
    artifact_id = f"8q-{asof.isoformat()}"
    log = structlog.get_logger(__name__).bind(ticker=ticker, analyzer=ANALYZER_TYPE)

    if use_cache:
        hit = analysis_cache.get(
            conn, analyzer_type=ANALYZER_TYPE, ticker=ticker, artifact_id=artifact_id
        )
        if hit is not None:
            log.info("cache_hit", artifact_id=artifact_id, age_s=int(hit.computed_at))
            return hit.response

    fund_df = _load_fundamentals(conn, ticker, asof, lookback_quarters=LOOKBACK_QUARTERS)
    ratios_df = _load_ratios(conn, ticker, asof, lookback_quarters=LOOKBACK_QUARTERS)
    if fund_df.empty and ratios_df.empty:
        log.warning("no_fundamentals_data")
        return None

    user_message = _build_user_message(ticker, asof, fund_df, ratios_df)
    system_blocks = [load_prompt("filing")]

    response = client.call(
        system_blocks=system_blocks,
        user_message=user_message,
        max_tokens=1200,
    )

    try:
        parsed = parse_json(response.text)
    except ValueError as exc:
        log.warning("parse_failed", error=str(exc), text_preview=response.text[:200])
        return None

    parsed = _validate_filing_response(parsed)

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
            ticker=ticker,
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


def estimate_run_cost(n_tickers: int) -> float:
    """Rough USD estimate for a fresh-cache run over N tickers.

    Used by ANAL-12 ``--estimate-cost``. Assumes:
      - System prompt cached after first call: ~6K chars (1.5K tokens) cache_write
        on call 1, then cache_read for calls 2..N.
      - Per-ticker user message: ~4K chars (1K tokens) at full input rate.
      - Output: ~800 chars (200 tokens).
    """
    if n_tickers <= 0:
        return 0.0
    sys_chars = 6000
    user_chars = 4000
    out_chars = 800
    # Call 1: cache_write on system + full user input + output
    first = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=0)
    # Approximate cache_write: input_chars but at 1.25x rate. estimate_cost has no
    # cache_write knob, so add it here directly using PriceTable defaults.
    from ls_equity_fund.analysis.cost_tracker import CostTracker

    write_cost = CostTracker.cost_of(
        input_tokens=0, output_tokens=0, cache_write_tokens=sys_chars // 4
    )
    first += write_cost
    # Calls 2..N: cache_read on system + full user input + output
    rest_each = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=sys_chars)
    return first + (n_tickers - 1) * rest_each


# --- helpers ----------------------------------------------------------------


def _load_fundamentals(
    conn: sqlite3.Connection, ticker: str, asof: date, *, lookback_quarters: int
) -> pd.DataFrame:
    """Pull the most recent N quarters of period-rows from ``fundamentals``.

    Fundamentals uses a WIDE schema — one column per metric. We PIT-collapse
    to the latest ``as_of_ingest_date`` per ``period_end`` (D2 binding from
    Phase 1).
    """
    cols = ", ".join(_FUND_COLUMNS)
    sql = f"""
        SELECT period_end, as_of_ingest_date, {cols}
        FROM fundamentals
        WHERE ticker = ?
          AND period_type = 'quarterly'
          AND period_end <= ?
        ORDER BY period_end DESC, as_of_ingest_date DESC
    """
    df = pd.read_sql_query(sql, conn, params=(ticker, asof.isoformat()))
    if df.empty:
        return df
    # PIT collapse: keep the freshest as_of_ingest_date per period_end.
    df = df.drop_duplicates(subset=["period_end"], keep="first")
    df = (
        df.head(lookback_quarters)
        .set_index("period_end")
        .drop(columns=["as_of_ingest_date"], errors="ignore")
    )
    # Sort ascending so the markdown table reads left→right oldest-to-newest.
    return df.sort_index()


def _load_ratios(
    conn: sqlite3.Connection, ticker: str, asof: date, *, lookback_quarters: int
) -> pd.DataFrame:
    """Pull derived ratios. ``fundamental_ratios`` uses ``asof_date`` instead
    of ``(period_end, period_type)``. We pull the most recent N rows."""
    cols = ", ".join(_RATIO_COLUMNS)
    sql = f"""
        SELECT asof_date, {cols}
        FROM fundamental_ratios
        WHERE ticker = ?
          AND asof_date <= ?
        ORDER BY asof_date DESC
    """
    df = pd.read_sql_query(sql, conn, params=(ticker, asof.isoformat()))
    if df.empty:
        return df
    df = df.head(lookback_quarters).set_index("asof_date").sort_index()
    return df


def _build_user_message(
    ticker: str,
    asof: date,
    fund_df: pd.DataFrame,
    ratios_df: pd.DataFrame,
) -> str:
    """Compose the per-ticker variable part of the prompt.

    Uses Markdown-table layout — Claude reads these well and they cost ~30%
    less tokens than JSON for the same data.
    """
    parts: list[str] = [f"Ticker: {ticker}", f"As-of date: {asof.isoformat()}", ""]
    if not fund_df.empty:
        parts.append("Fundamentals (most recent first; values in USD; period_end as index):")
        parts.append(fund_df.iloc[::-1].to_markdown(floatfmt=",.0f"))
        parts.append("")
    if not ratios_df.empty:
        parts.append("Derived ratios (most recent first):")
        parts.append(ratios_df.iloc[::-1].to_markdown(floatfmt=",.4f"))
        parts.append("")
    parts.append("Apply the rubric. Return only the JSON object specified in your instructions.")
    return "\n".join(parts)


def _validate_filing_response(obj: dict[str, Any]) -> dict[str, Any]:
    """Coerce + sanity-check the parsed Claude response.

    We don't reject a malformed response — analyzers degrade gracefully.
    Coerce numeric fields, default missing fields to neutral values, log
    silently. Phase 5 builds on this; better neutral than missing.
    """
    out: dict[str, Any] = {}
    out["earnings_quality_score"] = _clip_int(obj.get("earnings_quality_score"), 50)
    out["revenue_quality_score"] = _clip_int(obj.get("revenue_quality_score"), 50)
    out["balance_sheet_score"] = _clip_int(obj.get("balance_sheet_score"), 50)
    out["accruals_score"] = _clip_int(obj.get("accruals_score"), 50)
    out["red_flags"] = list(obj.get("red_flags") or [])[:10]
    out["green_flags"] = list(obj.get("green_flags") or [])[:10]
    out["risk_level"] = _clip_risk(obj.get("risk_level"))
    out["one_line_summary"] = str(obj.get("one_line_summary") or "")[:160]
    return out


def _clip_int(v: Any, default: int) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, n))


def _clip_risk(v: Any) -> str:
    if isinstance(v, str) and v.lower() in {"low", "medium", "high"}:
        return v.lower()
    return "medium"


__all__ = ["ANALYZER_TYPE", "LOOKBACK_QUARTERS", "analyze", "estimate_run_cost"]
