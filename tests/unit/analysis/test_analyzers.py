"""Behaviour tests for the 5 analyzers (filing, risk, insider, sector, earnings_call).

Exercises:
  - response-shape validation (the analyzer copes with malformed Claude output)
  - cache hit short-circuits the Claude call
  - artifact_id key derivation
  - Form 4 P/S-only filter assertion (CP3 binding for insider)
  - degenerate inputs return None where spec mandates None
"""

from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from ls_equity_fund.analysis import (
    cache as analysis_cache,
    earnings_call_analyzer,
    filing_analyzer,
    insider_analyzer,
    risk_analyzer,
    sector_analyzer,
)
from ls_equity_fund.analysis.claude_client import ClaudeClient, ClaudeResponse
from ls_equity_fund.analysis.cost_tracker import CostTracker

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _migrated_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "t.db"
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return sqlite3.connect(db_path)


def _mock_client(response_text: str) -> ClaudeClient:
    """A ClaudeClient whose .call() returns a fixed text without touching the SDK."""
    client = ClaudeClient(
        api_key="fake",
        model="claude-sonnet-4-5",
        cost_tracker=CostTracker(),
    )
    client.call = MagicMock(  # type: ignore[method-assign]
        return_value=ClaudeResponse(
            text=response_text,
            usage={
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 0,
            },
            stop_reason="end_turn",
        )
    )
    return client


# --- earnings_call (ANAL-11 stub) -------------------------------------------


def test_earnings_call_stub_returns_none(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    client = _mock_client("{}")
    out = earnings_call_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5)
    )
    assert out is None
    # And it must not have called Claude
    client.call.assert_not_called()


# --- filing analyzer (ANAL-05) ----------------------------------------------


def _seed_fundamentals(conn: sqlite3.Connection, ticker: str) -> None:
    """Seed 8 quarters of synthetic fundamentals + ratios.

    Schemas are WIDE (Phase 1 D2 mitigation): one column per metric, period_end
    is the index, as_of_ingest_date stamped for PIT replay.
    """
    asof = date(2026, 5, 5)
    quarters = [(asof - timedelta(days=90 * i)).isoformat() for i in range(8)]
    with conn:
        for q in quarters:
            conn.execute(
                "INSERT INTO fundamentals (ticker, period_end, period_type, "
                "as_of_ingest_date, revenue, net_income, cfo, total_assets, "
                "total_liabilities, total_equity, free_cash_flow, accruals, "
                "working_capital, gross_profit, operating_income, "
                "shares_outstanding) "
                "VALUES (?, ?, 'quarterly', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker, q, asof.isoformat(),
                    1_000_000.0, 100_000.0, 110_000.0,
                    5_000_000.0, 2_000_000.0, 3_000_000.0,
                    90_000.0, -10_000.0, 1_500_000.0,
                    400_000.0, 200_000.0, 1_000_000_000.0,
                ),
            )
            conn.execute(
                "INSERT INTO fundamental_ratios (ticker, asof_date, roe, net_margin, "
                "cfo_to_ni, debt_to_equity, gross_margin, operating_margin, "
                "revenue_growth_yoy, earnings_growth_yoy, current_ratio, accruals_ratio, roa) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker, q,
                    0.18, 0.10, 1.10, 0.40, 0.40, 0.20,
                    0.05, 0.08, 1.5, -0.02, 0.10,
                ),
            )


def test_filing_analyzer_persists_to_cache(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_fundamentals(conn, "AAPL")
    client = _mock_client(
        '{"earnings_quality_score": 80, "revenue_quality_score": 75, '
        '"balance_sheet_score": 85, "accruals_score": 70, '
        '"red_flags": [], "green_flags": ["high cfo/ni"], '
        '"risk_level": "low", "one_line_summary": "clean"}'
    )
    out = filing_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5), run_id="r1"
    )
    assert out is not None
    assert out["earnings_quality_score"] == 80
    assert out["risk_level"] == "low"

    # Cache hit on second run — Claude not called twice
    out2 = filing_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5)
    )
    assert out2 == out
    assert client.call.call_count == 1


def test_filing_analyzer_returns_none_when_no_data(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    # No fundamentals seeded
    client = _mock_client("{}")
    out = filing_analyzer.analyze(
        conn=conn, client=client, ticker="UNKNOWN", asof=date(2026, 5, 5)
    )
    assert out is None
    client.call.assert_not_called()


def test_filing_analyzer_validates_response_with_clipping(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_fundamentals(conn, "AAPL")
    # Out-of-range values + missing fields
    client = _mock_client(
        '{"earnings_quality_score": 150, "revenue_quality_score": -10, '
        '"risk_level": "extreme", "one_line_summary": "x"}'
    )
    out = filing_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5)
    )
    assert out is not None
    assert out["earnings_quality_score"] == 100  # clipped
    assert out["revenue_quality_score"] == 0  # clipped
    assert out["balance_sheet_score"] == 50  # default
    assert out["risk_level"] == "medium"  # invalid → default


# --- insider analyzer (ANAL-07) ---------------------------------------------


def _seed_form4(
    conn: sqlite3.Connection,
    ticker: str,
    rows: list[tuple[str, str, str, str, int, float]],
) -> None:
    """rows = (date, insider, title, code, shares, value).

    Phase 1 schema column names: ``insider_title`` not ``title``,
    ``price_per_share`` not ``price``, ``total_value`` not ``value``.
    """
    with conn:
        for i, (d, name, title, code, shares, value) in enumerate(rows):
            conn.execute(
                "INSERT INTO insider_transactions (accession_number, line_no, ticker, "
                "transaction_date, filed_date, insider_name, insider_title, "
                "transaction_code, shares, price_per_share, total_value) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"acc-{i}",
                    1,
                    ticker,
                    d,
                    d,
                    name,
                    title,
                    code,
                    float(shares),
                    1.0,
                    float(value),
                ),
            )


def test_insider_analyzer_returns_none_with_no_activity(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    client = _mock_client("{}")
    out = insider_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5)
    )
    assert out is None
    client.call.assert_not_called()


def test_insider_analyzer_runs_on_p_purchase(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_form4(
        conn,
        "AAPL",
        [
            ("2026-04-15", "Tim Cook", "CEO", "P", 1000, 200_000.0),
            ("2026-04-10", "Luca Maestri", "CFO", "P", 500, 100_000.0),
        ],
    )
    client = _mock_client(
        '{"signal": "STRONG_BUY", "confidence": 0.85, '
        '"key_transactions": [], "reasoning": "ceo and cfo cluster", '
        '"one_line_summary": "strong buying"}'
    )
    out = insider_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5)
    )
    assert out is not None
    assert out["signal"] == "STRONG_BUY"
    assert 0.0 <= out["confidence"] <= 1.0


def test_insider_user_message_distinguishes_p_s_from_noise(tmp_path: Path) -> None:
    """CP3 binding — A/M/F/G/D must be tagged as 'noise'."""
    conn = _migrated_db(tmp_path)
    _seed_form4(
        conn,
        "AAPL",
        [
            ("2026-04-15", "Insider1", "CEO", "P", 1000, 200_000.0),
            ("2026-04-12", "Insider2", "Director", "A", 500, 100_000.0),  # award (noise)
            ("2026-04-10", "Insider1", "CEO", "M", 200, 40_000.0),  # exercise (noise)
        ],
    )
    client = _mock_client('{"signal": "BUY", "confidence": 0.6}')
    insider_analyzer.analyze(
        conn=conn, client=client, ticker="AAPL", asof=date(2026, 5, 5)
    )
    # Inspect the user message that was passed to client.call
    args, kwargs = client.call.call_args
    user_msg = kwargs["user_message"]
    assert "1 transactions" in user_msg  # 1 P
    assert "do NOT factor into directional signal" in user_msg
    assert "2 transactions" in user_msg  # the A/M noise


# --- risk analyzer section extractor ---------------------------------------


def test_risk_extract_handles_html(tmp_path: Path) -> None:
    sample_html = b"""<html><body>
    <p>Some preamble (table of contents)</p>
    <h2>Item 1A. Risk Factors</h2>
    <p>Index reference here only.</p>
    <h1>Item 1A. Risk Factors</h1>
    <p>The actual body. Paragraph 1 with concrete risk details.</p>
    <p>Paragraph 2 with more concrete details.</p>
    <h2>Item 1B. Unresolved Staff Comments</h2>
    <p>Should NOT appear in extraction.</p>
    </body></html>"""
    p = tmp_path / "filing.html"
    p.write_bytes(sample_html)
    text = risk_analyzer.extract_risk_factors(p)
    assert "actual body" in text
    assert "Paragraph 2" in text
    assert "Should NOT appear" not in text


def test_risk_extract_returns_empty_on_missing_section(tmp_path: Path) -> None:
    p = tmp_path / "filing.html"
    p.write_bytes(b"<html><body><p>No risk section here.</p></body></html>")
    assert risk_analyzer.extract_risk_factors(p) == ""


def test_risk_extract_returns_empty_on_missing_file(tmp_path: Path) -> None:
    assert risk_analyzer.extract_risk_factors(tmp_path / "nope.html") == ""


# --- sector analyzer (ANAL-08) ---------------------------------------------


def test_sector_analyzer_returns_none_when_no_candidates(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    client = _mock_client("{}")
    out = sector_analyzer.analyze(
        conn=conn,
        client=client,
        sector="Information Technology",
        asof=date(2026, 5, 5),
    )
    assert out is None
    client.call.assert_not_called()


def test_sector_analyzer_round_trips(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    now = int(time.time())
    with conn:
        for ticker, score in (("AAPL", 85.0), ("MSFT", 70.0), ("NVDA", 60.0)):
            for factor in ("momentum", "value", "combined"):
                conn.execute(
                    "INSERT INTO factor_scores_parent (ticker, score_date, factor, "
                    "parent_score, sector, n_subfactors_used, computed_at) "
                    "VALUES (?, ?, ?, ?, 'Information Technology', 6, ?)",
                    (ticker, asof.isoformat(), factor, score, now),
                )
    client = _mock_client(
        '{"sector": "Information Technology", '
        '"top_long_idea": {"ticker": "AAPL", "thesis": "x", "key_drivers": [], "risk_to_thesis": "y"}, '
        '"top_short_idea": {"ticker": "NVDA", "thesis": "x", "key_drivers": [], "risk_to_thesis": "y"}, '
        '"sector_outlook": "ok", "outlook_stance": "neutral", "one_line_summary": "x"}'
    )
    out = sector_analyzer.analyze(
        conn=conn,
        client=client,
        sector="Information Technology",
        asof=asof,
    )
    assert out is not None
    assert out["top_long_idea"]["ticker"] == "AAPL"
    assert out["top_short_idea"]["ticker"] == "NVDA"
    assert out["outlook_stance"] == "neutral"


def test_sector_validation_falls_back_on_bad_stance(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    asof = date(2026, 5, 5)
    now = int(time.time())
    with conn:
        for ticker, score in (("AAPL", 85.0), ("MSFT", 70.0)):
            conn.execute(
                "INSERT INTO factor_scores_parent (ticker, score_date, factor, "
                "parent_score, sector, n_subfactors_used, computed_at) "
                "VALUES (?, ?, 'combined', ?, 'IT', 6, ?)",
                (ticker, asof.isoformat(), score, now),
            )
    client = _mock_client(
        '{"sector": "IT", '
        '"top_long_idea": null, "top_short_idea": null, '
        '"outlook_stance": "wildly_bullish"}'
    )
    out = sector_analyzer.analyze(conn=conn, client=client, sector="IT", asof=asof)
    assert out is not None
    assert out["outlook_stance"] == "neutral"  # invalid → default
    # idea coercion when None passed
    assert out["top_long_idea"]["ticker"] == ""


# --- estimate_run_cost surfaces are sane ------------------------------------


@pytest.mark.parametrize(
    "module",
    [filing_analyzer, risk_analyzer, insider_analyzer, sector_analyzer],
)
def test_estimate_run_cost_scales_with_n(module: object) -> None:
    fn = module.estimate_run_cost  # type: ignore[attr-defined]
    assert fn(0) == 0.0
    one = fn(1)
    forty = fn(40)
    assert 0 < one < forty
    # Each subsequent call costs less than the first (cache benefit)
    average_post_first = (forty - one) / 39
    assert average_post_first < one


def test_earnings_call_estimate_is_zero() -> None:
    assert earnings_call_analyzer.estimate_run_cost(40) == 0.0


# --- cache_hit short-circuits the run ---------------------------------------


def test_filing_analyzer_cache_hit_skips_claude(tmp_path: Path) -> None:
    conn = _migrated_db(tmp_path)
    _seed_fundamentals(conn, "AAPL")
    asof = date(2026, 5, 5)
    # Pre-warm cache
    analysis_cache.put(
        conn,
        analyzer_type="filing",
        ticker="AAPL",
        artifact_id=f"8q-{asof.isoformat()}",
        run_id="prior",
        model="claude-sonnet-4-5",
        response={"earnings_quality_score": 99, "risk_level": "low"},
        input_tokens=0,
        output_tokens=0,
    )
    client = _mock_client("{}")
    out = filing_analyzer.analyze(conn=conn, client=client, ticker="AAPL", asof=asof)
    assert out is not None
    assert out["earnings_quality_score"] == 99
    client.call.assert_not_called()
