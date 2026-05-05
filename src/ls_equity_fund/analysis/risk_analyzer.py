"""ANAL-06 — Risk Factors analyzer.

Pulls the latest 10-K + the prior-year 10-K from Phase 1's filings_metadata
+ filesystem-backed bodies, extracts Item 1A (Risk Factors), strips HTML,
caps at 80K chars, and asks Claude to:
  - separate material risks from boilerplate
  - flag NEW risks vs prior-year filing
  - score boilerplate_percentage and severity

Caching keyed on ``(ticker, accession_number)`` of the latest 10-K.
Re-running for the same filing is free; new 10-K invalidates naturally.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import structlog
from bs4 import BeautifulSoup

from ls_equity_fund.analysis import cache as analysis_cache
from ls_equity_fund.analysis.claude_client import (
    ClaudeClient,
    estimate_cost,
    load_prompt,
    parse_json,
)

log = structlog.get_logger(__name__)

ANALYZER_TYPE = "risk"
MAX_RISK_CHARS = 80_000  # ANAL-06 spec
PRIOR_RISK_CHARS = 60_000  # cap prior to keep diff prompt reasonable


def analyze(
    *,
    conn: sqlite3.Connection,
    client: ClaudeClient,
    ticker: str,
    run_id: str | None = None,
    use_cache: bool = True,
    ttl_days: int = analysis_cache.DEFAULT_TTL_DAYS,
) -> dict[str, Any] | None:
    """Run risk analyzer for one ticker; return parsed JSON or None.

    None when ticker has no 10-K in filings_metadata.
    """
    log = structlog.get_logger(__name__).bind(ticker=ticker, analyzer=ANALYZER_TYPE)

    latest = _latest_10k(conn, ticker)
    if latest is None:
        log.warning("no_10k_filing")
        return None

    artifact_id = latest["accession_number"]

    if use_cache:
        hit = analysis_cache.get(
            conn, analyzer_type=ANALYZER_TYPE, ticker=ticker, artifact_id=artifact_id
        )
        if hit is not None:
            log.info("cache_hit", accession=artifact_id)
            return hit.response

    risk_text = extract_risk_factors(Path(latest["filepath"]))
    if not risk_text:
        log.warning("no_risk_factors_extracted", filepath=latest["filepath"])
        return None
    risk_text = risk_text[:MAX_RISK_CHARS]

    # Optional prior 10-K for new-vs-prior diff
    prior = _prior_10k(conn, ticker, before=latest["filed_date"])
    prior_text = ""
    if prior is not None:
        prior_text = extract_risk_factors(Path(prior["filepath"]))[:PRIOR_RISK_CHARS]

    user_message = _build_user_message(ticker, latest, risk_text, prior, prior_text)
    system_blocks = [load_prompt("risk")]

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

    parsed = _validate_risk_response(parsed)

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
    """Risk analyzer cost dominates — 10-K text is huge."""
    if n_tickers <= 0:
        return 0.0
    sys_chars = 4000
    # Average risk text is ~50K chars; some shorter, some hit 80K cap
    user_chars = 60_000
    out_chars = 1500
    first = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=0)
    from ls_equity_fund.analysis.cost_tracker import CostTracker

    write_cost = CostTracker.cost_of(
        input_tokens=0, output_tokens=0, cache_write_tokens=sys_chars // 4
    )
    first += write_cost
    rest = estimate_cost(input_chars=user_chars, output_chars=out_chars, cache_chars=sys_chars)
    return first + (n_tickers - 1) * rest


# --- HTML / text extraction -----------------------------------------------


# Risk Factors header anchors. 10-K item 1A. Try multiple variants because
# some filers use "ITEM 1A.", others "Item 1A.", others wrap in their own
# H1/H2. The cut goes from the start of the match to the start of "Item 1B"
# or "Item 2" (whichever comes first). Matching is case-insensitive.
_RISK_START_RE = re.compile(r"item\s+1a\W+risk\s+factors\b", re.IGNORECASE)
_RISK_END_RE = re.compile(r"item\s+1b\b|item\s+2\b|unresolved\s+staff\s+comments\b", re.IGNORECASE)


def extract_risk_factors(filing_path: Path) -> str:
    """Read filing body, strip HTML, return Risk Factors section as plain text.

    Returns "" if no Item 1A header detected — the analyzer treats that as a
    skip signal. This is intentionally forgiving: filings use various item-
    header conventions and we'd rather skip a single ticker than crash the run.
    """
    if not filing_path.exists():
        return ""

    raw = filing_path.read_bytes()
    text = _strip_html(raw)
    return _slice_risk_section(text)


def _strip_html(raw: bytes) -> str:
    """BeautifulSoup4 → plain text. Falls back to bytes-decode on parse error."""
    try:
        soup = BeautifulSoup(raw, "html.parser")
        # Drop scripts/styles
        for tag in soup(("script", "style")):
            tag.decompose()
        return soup.get_text(separator="\n")
    except Exception:
        try:
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return ""


def _slice_risk_section(text: str) -> str:
    if not text:
        return ""
    # Find FIRST occurrence of "Item 1A. Risk Factors" — earlier hits in TOC
    # are intentional anchors, not the actual body. We want the SECOND match if
    # it exists (TOC + body); else fall back to first.
    starts = list(_RISK_START_RE.finditer(text))
    if not starts:
        return ""
    start_match = starts[1] if len(starts) >= 2 else starts[0]
    body_start = start_match.start()
    end_match = _RISK_END_RE.search(text, pos=body_start + 50)
    end = end_match.start() if end_match else len(text)
    section = text[body_start:end].strip()
    # Compact whitespace
    section = re.sub(r"\n{3,}", "\n\n", section)
    section = re.sub(r"[ \t]{2,}", " ", section)
    return section


# --- DB helpers ----------------------------------------------------------


def _latest_10k(conn: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    cur = conn.execute(
        """
        SELECT accession_number, ticker, form_type, filed_date, period_of_report,
               filepath
        FROM filings_metadata
        WHERE ticker = ? AND form_type = '10-K'
        ORDER BY filed_date DESC
        LIMIT 1
        """,
        (ticker,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "accession_number": row[0],
        "ticker": row[1],
        "form_type": row[2],
        "filed_date": row[3],
        "period_of_report": row[4],
        "filepath": row[5],
    }


def _prior_10k(conn: sqlite3.Connection, ticker: str, *, before: str) -> dict[str, Any] | None:
    cur = conn.execute(
        """
        SELECT accession_number, filed_date, period_of_report, filepath
        FROM filings_metadata
        WHERE ticker = ? AND form_type = '10-K' AND filed_date < ?
        ORDER BY filed_date DESC
        LIMIT 1
        """,
        (ticker, before),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "accession_number": row[0],
        "filed_date": row[1],
        "period_of_report": row[2],
        "filepath": row[3],
    }


def _build_user_message(
    ticker: str,
    latest: dict[str, Any],
    risk_text: str,
    prior: dict[str, Any] | None,
    prior_text: str,
) -> str:
    parts = [
        f"Ticker: {ticker}",
        f"Latest 10-K filed: {latest['filed_date']}  (period: {latest.get('period_of_report') or 'unknown'})",
        f"Latest accession: {latest['accession_number']}",
        "",
        "=== Latest Risk Factors (Item 1A) ===",
        risk_text,
    ]
    if prior is not None and prior_text:
        parts.extend(
            [
                "",
                f"=== Prior Year Risk Factors (10-K filed {prior['filed_date']}) ===",
                prior_text,
            ]
        )
    else:
        parts.extend(
            [
                "",
                "=== No Prior 10-K available ===",
                "(set new_risks=[] and surface concrete risks under material_risks)",
            ]
        )
    parts.extend(
        [
            "",
            "Apply the rubric. Return only the JSON object specified in your instructions.",
        ]
    )
    return "\n".join(parts)


def _validate_risk_response(obj: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["new_risks"] = list(obj.get("new_risks") or [])[:15]
    out["material_risks"] = list(obj.get("material_risks") or [])[:15]
    bp = obj.get("boilerplate_percentage")
    try:
        out["boilerplate_percentage"] = max(0, min(100, int(float(bp))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        out["boilerplate_percentage"] = 50
    sev = obj.get("risk_severity")
    out["risk_severity"] = (
        sev.lower()
        if isinstance(sev, str) and sev.lower() in {"low", "medium", "high", "critical"}
        else "medium"
    )
    out["one_line_summary"] = str(obj.get("one_line_summary") or "")[:160]
    return out


__all__ = [
    "ANALYZER_TYPE",
    "MAX_RISK_CHARS",
    "analyze",
    "estimate_run_cost",
    "extract_risk_factors",
]
