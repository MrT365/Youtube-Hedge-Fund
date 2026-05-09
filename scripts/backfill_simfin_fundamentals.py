#!/usr/bin/env python3
"""Backfill point-in-time fundamentals from SimFin into the local DB.

The default ``meridian run-data`` pipeline uses yfinance, which only ships
*today's* fundamental snapshot — that breaks Phase A historical IC validation
because every replay date sees the same scores. SimFin ships per-statement
``Publish Date`` alongside ``Report Date``, which is the canonical PIT
contract: at any historical date T, only rows with ``publish_date <= T`` were
"knowable" — that's exactly what the existing append-only ``as_of_ingest_date``
column on the ``fundamentals`` table represents.

This script:
  1. Loads the universe (active tickers) from SQLite.
  2. Bulk-downloads SimFin's quarterly + annual income / balance / cashflow.
  3. Filters to the universe.
  4. Maps SimFin column names to our 28-column ``SCHEMA_COLS`` schema.
  5. INSERT OR IGNOREs each (ticker, period_end, period_type, publish_date)
     row into the ``fundamentals`` table — using ``Publish Date`` as
     ``as_of_ingest_date`` so historical replay reads PIT-correct values.

Idempotent: re-running drops nothing, only fills gaps. The PK
(ticker, period_end, period_type, as_of_ingest_date) dedups on collision.

Usage:
  uv run python scripts/backfill_simfin_fundamentals.py
  uv run python scripts/backfill_simfin_fundamentals.py --variant annual
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ls_equity_fund.config import load_config
from ls_equity_fund.db import get_connection, get_db_path

# --- SimFin column name → our schema column mapping --------------------------
# Some target schema columns aren't in SimFin's standard datasets and stay
# NULL: eps_basic, eps_diluted (computed downstream), free_cash_flow (derived),
# capex (we use Change in Fixed Assets & Intangibles which is closest), buybacks
# (we use Cash from (Repurchase of) Equity), accruals (computed downstream),
# working_capital (derived), rd_expense (R&D directly mapped).
INCOME_MAP: dict[str, str] = {
    "Revenue": "revenue",
    "Gross Profit": "gross_profit",
    "Operating Income (Loss)": "operating_income",
    "Net Income": "net_income",
    "Research & Development": "rd_expense",
}
BALANCE_MAP: dict[str, str] = {
    "Total Assets": "total_assets",
    "Total Liabilities": "total_liabilities",
    "Total Equity": "total_equity",
    "Total Current Assets": "current_assets",
    "Total Current Liabilities": "current_liabilities",
    "Accounts & Notes Receivable": "accounts_receivable",
    "Inventories": "inventory",
    "Long Term Debt": "long_term_debt",
    "Cash, Cash Equivalents & Short Term Investments": "cash_and_equivalents",
    "Retained Earnings": "retained_earnings",
}
CASHFLOW_MAP: dict[str, str] = {
    "Net Cash from Operating Activities": "cfo",
    "Net Cash from Investing Activities": "cfi",
    "Net Cash from Financing Activities": "cff",
    "Change in Fixed Assets & Intangibles": "capex",
    "Dividends Paid": "dividends_paid",
    "Cash from (Repurchase of) Equity": "buybacks",
}

SCHEMA_COLS: tuple[str, ...] = (
    "revenue", "gross_profit", "operating_income", "net_income",
    "eps_basic", "eps_diluted",
    "total_assets", "total_liabilities", "total_equity",
    "current_assets", "current_liabilities",
    "accounts_receivable", "inventory",
    "long_term_debt", "cash_and_equivalents",
    "cfo", "cfi", "cff", "capex", "free_cash_flow",
    "dividends_paid", "buybacks", "shares_outstanding",
    "rd_expense", "ebit", "retained_earnings",
    "working_capital", "accruals",
)


def _coerce(v: Any) -> float | None:
    """NaN/None-safe float coercion."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _load_universe(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM universe WHERE delisted_date IS NULL"
        )
    }


def _build_period_rows(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    period_type: str,
    universe: set[str],
) -> list[dict[str, Any]]:
    """Combine the three statements into one row per (ticker, period_end,
    publish_date) record."""
    # SimFin: ('Ticker', 'Report Date') is the index. Reset for filtering/joining.
    inc = income.reset_index()
    bal = balance.reset_index()
    cf = cashflow.reset_index()

    inc = inc[inc["Ticker"].isin(universe)]
    bal = bal[bal["Ticker"].isin(universe)]
    cf = cf[cf["Ticker"].isin(universe)]

    # Outer-merge on (Ticker, Report Date) so a missing balance/cashflow row
    # doesn't drop the income row (rare but happens with restated filings).
    merged = inc.merge(
        bal,
        on=["Ticker", "Report Date"],
        how="outer",
        suffixes=("_inc", "_bal"),
    ).merge(
        cf,
        on=["Ticker", "Report Date"],
        how="outer",
    )

    # Pick the earliest non-null Publish Date across the three statements.
    pub_cols = [c for c in merged.columns if c.startswith("Publish Date")]
    merged["_publish"] = merged[pub_cols].bfill(axis=1).iloc[:, 0]
    merged = merged.dropna(subset=["_publish"])

    rows: list[dict[str, Any]] = []
    for _, r in merged.iterrows():
        period_end = pd.to_datetime(r["Report Date"]).date().isoformat()
        publish_date = pd.to_datetime(r["_publish"]).date().isoformat()
        out: dict[str, Any] = {
            "ticker": r["Ticker"],
            "period_end": period_end,
            "period_type": period_type,
            "as_of_ingest_date": publish_date,
        }
        # Apply the three column maps.
        for src_col, dst_col in {**INCOME_MAP, **BALANCE_MAP, **CASHFLOW_MAP}.items():
            out[dst_col] = _coerce(r.get(src_col))
        # Shares — pull from income statement first, balance as fallback.
        shares = r.get("Shares (Diluted)") or r.get("Shares (Basic)") or r.get("Shares (Diluted)_inc")
        out["shares_outstanding"] = _coerce(shares)
        # Free cash flow ≈ CFO − CapEx (Change in Fixed Assets is signed).
        cfo_v = out.get("cfo")
        capex_v = out.get("capex")
        if cfo_v is not None and capex_v is not None:
            out["free_cash_flow"] = cfo_v + capex_v  # capex is negative on cashflow stmt
        # Working capital ≈ current assets − current liabilities.
        ca, cl = out.get("current_assets"), out.get("current_liabilities")
        if ca is not None and cl is not None:
            out["working_capital"] = ca - cl
        # EBIT ≈ operating income (close enough for our factor purposes).
        if out.get("ebit") is None:
            out["ebit"] = out.get("operating_income")
        rows.append(out)
    return rows


def _persist(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """INSERT OR IGNORE into fundamentals. Returns rowcount written."""
    if not rows:
        return 0
    cols = ["ticker", "period_end", "period_type", "as_of_ingest_date", *SCHEMA_COLS]
    placeholders = ", ".join(["?"] * len(cols))
    sql = (
        f"INSERT OR IGNORE INTO fundamentals ({', '.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    payload = [tuple(r.get(c) for c in cols) for r in rows]
    with conn:
        cur = conn.executemany(sql, payload)
    return cur.rowcount or 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variants", nargs="+", default=["quarterly", "annual"],
        choices=["quarterly", "annual"],
    )
    args = parser.parse_args()

    import simfin as sf

    config, secrets = load_config()
    if not secrets.simfin_api_key:
        print("ERROR: SIMFIN_API_KEY not set in .env", file=sys.stderr)
        return 1

    sf.set_api_key(secrets.simfin_api_key)
    sf.set_data_dir(str(REPO_ROOT / "cache" / "simfin_data"))

    db_path = get_db_path(config)
    conn = get_connection(db_path)
    try:
        universe = _load_universe(conn)
        print(f"universe: {len(universe)} active tickers")

        total_written = 0
        for variant in args.variants:
            print(f"\n=== variant={variant} ===")
            print("loading SimFin datasets (cached after first run)...")
            inc = sf.load_income(variant=variant, market="us")
            bal = sf.load_balance(variant=variant, market="us")
            cf = sf.load_cashflow(variant=variant, market="us")
            print(f"  income:   {len(inc):>7} rows")
            print(f"  balance:  {len(bal):>7} rows")
            print(f"  cashflow: {len(cf):>7} rows")

            rows = _build_period_rows(
                inc, bal, cf, period_type=variant, universe=universe
            )
            print(f"  built {len(rows)} (ticker,period,publish) rows for our universe")

            written = _persist(conn, rows)
            total_written += written
            print(f"  wrote {written} new rows (existing PK collisions skipped)")

        print(f"\nDONE — total new fundamentals rows: {total_written}")
        # Final inventory.
        n_total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
        n_dates = conn.execute(
            "SELECT COUNT(DISTINCT as_of_ingest_date) FROM fundamentals"
        ).fetchone()[0]
        n_publish_distinct = conn.execute(
            "SELECT COUNT(DISTINCT (ticker || ':' || period_end)) FROM fundamentals"
        ).fetchone()[0]
        print(f"  fundamentals table now: {n_total} rows, "
              f"{n_dates} distinct publish dates, "
              f"{n_publish_distinct} (ticker,period_end) cells")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
