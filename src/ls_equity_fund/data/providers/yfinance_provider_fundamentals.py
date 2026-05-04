"""yfinance fundamentals extraction (DATA-04, FundamentalsProvider impl).

Pulls annual + quarterly income/balance/cash-flow via yfinance Ticker properties,
normalizes row labels, and returns MultiIndex DataFrame matching the
fundamentals table schema in migration 0002.

D2 mitigation note: This module produces the per-period rows. The orchestrator
(``data/fundamentals.py``) is what stamps each row with today's
``as_of_ingest_date`` and writes APPEND-ONLY via INSERT OR IGNORE — never
UPDATE, never UPSERT. yfinance restating a value tomorrow → tomorrow's row
appended; yesterday's value preserved. See PITFALLS.md D2 (CRITICAL).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

# Map yfinance row labels (as seen in 2026 yfinance 0.2.65) to schema columns.
# yfinance label keys are subject to drift across minor versions; integration
# tests (Plan 10) will surface drift. For unknowns, fall back to None.
_YF_LABEL_MAP: dict[str, str] = {
    "Total Revenue": "revenue",
    "Gross Profit": "gross_profit",
    "Operating Income": "operating_income",
    "Net Income": "net_income",
    "Basic EPS": "eps_basic",
    "Diluted EPS": "eps_diluted",
    "Total Assets": "total_assets",
    "Total Liabilities Net Minority Interest": "total_liabilities",
    "Stockholders Equity": "total_equity",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Accounts Receivable": "accounts_receivable",
    "Inventory": "inventory",
    "Long Term Debt": "long_term_debt",
    "Cash And Cash Equivalents": "cash_and_equivalents",
    "Operating Cash Flow": "cfo",
    "Investing Cash Flow": "cfi",
    "Financing Cash Flow": "cff",
    "Capital Expenditure": "capex",
    "Free Cash Flow": "free_cash_flow",
    "Cash Dividends Paid": "dividends_paid",
    "Repurchase Of Capital Stock": "buybacks",
    "Share Issued": "shares_outstanding",
    "Research And Development": "rd_expense",
    "EBIT": "ebit",
    "Retained Earnings": "retained_earnings",
    "Working Capital": "working_capital",
}

SCHEMA_COLS: list[str] = [
    "revenue", "gross_profit", "operating_income", "net_income",
    "eps_basic", "eps_diluted", "total_assets", "total_liabilities",
    "total_equity", "current_assets", "current_liabilities",
    "accounts_receivable", "inventory", "long_term_debt",
    "cash_and_equivalents", "cfo", "cfi", "cff", "capex",
    "free_cash_flow", "dividends_paid", "buybacks", "shares_outstanding",
    "rd_expense", "ebit", "retained_earnings", "working_capital", "accruals",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
def get_fundamentals_impl(session: Any, ticker: str) -> pd.DataFrame:
    """Return MultiIndex(['period_end', 'period_type']) DataFrame with SCHEMA_COLS.

    Pulls Ticker.income_stmt + .balance_sheet + .cashflow (annual) and the
    quarterly_* variants. yfinance returns wide-form DataFrames keyed by row
    label; we melt into long-form per-period rows, then pivot back into a
    schema-aligned per-period frame.

    Empty result (e.g., yfinance returns no statements) returns an empty
    DataFrame with the correct MultiIndex shape — caller writes 0 rows and
    moves on.
    """
    import yfinance as yf

    yt = yf.Ticker(ticker, session=session) if session else yf.Ticker(ticker)
    frames: list[pd.DataFrame] = []

    for period_type, attrs in (
        ("annual", ("income_stmt", "balance_sheet", "cashflow")),
        ("quarterly", ("quarterly_income_stmt", "quarterly_balance_sheet", "quarterly_cashflow")),
    ):
        merged: dict[Any, dict[str, float]] = {}
        for attr in attrs:
            try:
                df = getattr(yt, attr)
            except Exception as e:
                log.warning("yf_fundamentals_attr_failed", ticker=ticker, attr=attr, error=str(e))
                continue
            if df is None or df.empty:
                continue
            for label, schema_col in _YF_LABEL_MAP.items():
                if label not in df.index:
                    continue
                series = df.loc[label]
                for period_end, value in series.items():
                    # NaN check — float('nan') != float('nan')
                    try:
                        if value != value:  # noqa: PLR0124 — NaN check
                            continue
                    except TypeError:
                        continue
                    merged.setdefault(period_end, {})[schema_col] = float(value)

        for period_end, fields in merged.items():
            row: dict[str, Any] = {col: fields.get(col) for col in SCHEMA_COLS}
            # Compute accruals = NI - CFO if both present
            if row.get("net_income") is not None and row.get("cfo") is not None:
                row["accruals"] = row["net_income"] - row["cfo"]
            row["period_end"] = pd.Timestamp(period_end).date().isoformat()
            row["period_type"] = period_type
            frames.append(pd.DataFrame([row]))

    if not frames:
        empty = pd.DataFrame(columns=SCHEMA_COLS)
        empty.index = pd.MultiIndex.from_tuples([], names=["period_end", "period_type"])
        return empty

    out = pd.concat(frames, ignore_index=True).set_index(["period_end", "period_type"])
    return out[SCHEMA_COLS]


__all__ = ["SCHEMA_COLS", "get_fundamentals_impl"]
