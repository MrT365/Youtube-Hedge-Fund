"""ANAL-07 — Insider analyzer.

Pulls last-90-day Form 4 transactions from Phase 1's ``insider_transactions``,
formats them as a compact table for Claude, and returns a STRONG_BUY..STRONG_SELL
signal with confidence + reasoning.

CRITICAL — CP3 binding (Form 4 misclassification):
  Only P (purchase) and S (sale) carry directional signal. A/M/F/G/D MUST NOT
  influence the signal. The prompt enforces this; the analyzer pre-filters
  aggressive stats but ALSO sends raw rows so Claude can verify.

Returns None if there's no Form 4 activity in the 90-day window — the spec
says "returns None if no insider data" so downstream Phase 5 ANAL-09 falls
back to 100% quant.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
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

ANALYZER_TYPE = "insider"
WINDOW_DAYS = 90

VALID_SIGNALS = ("STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL")


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
    """Run insider analyzer for one ticker; return parsed JSON or None."""
    log = structlog.get_logger(__name__).bind(ticker=ticker, analyzer=ANALYZER_TYPE)

    artifact_id = f"insider-{asof.isoformat()}-{WINDOW_DAYS}d"

    if use_cache:
        hit = analysis_cache.get(
            conn, analyzer_type=ANALYZER_TYPE, ticker=ticker, artifact_id=artifact_id
        )
        if hit is not None:
            log.info("cache_hit", artifact_id=artifact_id)
            return hit.response

    txns = _load_form4_transactions(conn, ticker, asof, window_days=WINDOW_DAYS)
    if txns.empty:
        log.info("no_insider_activity")
        return None

    user_message = _build_user_message(ticker, asof, txns)
    system_blocks = [load_prompt("insider")]

    response = client.call(
        system_blocks=system_blocks,
        user_message=user_message,
        max_tokens=1200,
    )
    try:
        parsed = parse_json(response.text)
    except ValueError as exc:
        log.warning("parse_failed", error=str(exc))
        return None

    parsed = _validate_insider_response(parsed)

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
    if n_tickers <= 0:
        return 0.0
    sys_chars = 4500
    user_chars = 2500  # 90 days x ~3 transactions x short rows
    out_chars = 800
    first = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=0)
    from ls_equity_fund.analysis.cost_tracker import CostTracker

    write_cost = CostTracker.cost_of(
        input_tokens=0, output_tokens=0, cache_write_tokens=sys_chars // 4
    )
    first += write_cost
    rest = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=sys_chars)
    return first + (n_tickers - 1) * rest


# --- helpers ----------------------------------------------------------------


def _load_form4_transactions(
    conn: sqlite3.Connection, ticker: str, asof: date, *, window_days: int
) -> pd.DataFrame:
    start = (asof - timedelta(days=window_days)).isoformat()
    end = asof.isoformat()
    sql = """
        SELECT transaction_date, insider_name, insider_title AS title,
               transaction_code, shares, price_per_share AS price,
               total_value AS value
        FROM insider_transactions
        WHERE ticker = ?
          AND transaction_date BETWEEN ? AND ?
        ORDER BY transaction_date DESC
    """
    df = pd.read_sql_query(sql, conn, params=(ticker, start, end))
    return df


def _build_user_message(ticker: str, asof: date, txns: pd.DataFrame) -> str:
    """Compose user msg with both summary stats AND raw rows.

    Why both: summary stats let Claude reason fast on aggregate flow; raw rows
    let it verify P/S/A/M/F/G/D semantics directly (CP3 binding). The prompt
    instructs the model to trust the raw rows.
    """
    p_count = int((txns["transaction_code"] == "P").sum())
    s_count = int((txns["transaction_code"] == "S").sum())
    other_count = int(len(txns) - p_count - s_count)
    p_value = float(txns.loc[txns["transaction_code"] == "P", "value"].sum() or 0)
    s_value = float(txns.loc[txns["transaction_code"] == "S", "value"].sum() or 0)

    distinct_p_buyers = txns.loc[txns["transaction_code"] == "P", "insider_name"].nunique()

    parts = [
        f"Ticker: {ticker}",
        f"Window: {(asof - timedelta(days=WINDOW_DAYS)).isoformat()} → {asof.isoformat()} (last {WINDOW_DAYS} days)",
        "",
        f"Summary — {len(txns)} total Form 4 rows:",
        f"  P (purchase):     {p_count} transactions, ${p_value:,.0f} aggregate (distinct insiders: {distinct_p_buyers})",
        f"  S (sale):         {s_count} transactions, ${s_value:,.0f} aggregate",
        f"  A/M/F/G/D (noise): {other_count} transactions  (do NOT factor into directional signal)",
        "",
        "Raw rows (most recent first):",
        txns.to_markdown(index=False, floatfmt=",.2f"),
        "",
        "Apply the rubric. Return only the JSON object specified in your instructions.",
    ]
    return "\n".join(parts)


def _validate_insider_response(obj: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sig = obj.get("signal")
    out["signal"] = sig if isinstance(sig, str) and sig in VALID_SIGNALS else "NEUTRAL"
    try:
        conf = float(obj.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    out["confidence"] = max(0.0, min(1.0, conf))
    raw_txns = obj.get("key_transactions")
    out["key_transactions"] = list(raw_txns) if isinstance(raw_txns, list) else []
    out["reasoning"] = str(obj.get("reasoning") or "")[:1000]
    out["one_line_summary"] = str(obj.get("one_line_summary") or "")[:160]
    return out


__all__ = [
    "ANALYZER_TYPE",
    "VALID_SIGNALS",
    "WINDOW_DAYS",
    "analyze",
    "estimate_run_cost",
]
