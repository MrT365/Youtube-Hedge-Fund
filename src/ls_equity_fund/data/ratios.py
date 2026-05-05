"""Compute the 24 derived fundamental ratios per DATA-04.

Reads ``fundamentals`` (latest as_of_ingest_date per period) + ``daily_prices``
(latest close on or before asof) and writes to ``fundamental_ratios``.

The 24 ratios (from REQUIREMENTS.md DATA-04, in fundamental_ratios PK order):

    roe                     = net_income / total_equity
    roa                     = net_income / total_assets
    gross_margin            = gross_profit / revenue
    operating_margin        = operating_income / revenue
    net_margin              = net_income / revenue
    revenue_growth_yoy      = (rev_t0 / rev_t-4q) - 1   (quarterly preferred)
                              fallback: (rev_yr0 / rev_yr-1) - 1 if <5 quarters
    revenue_growth_qoq      = (rev_t0 / rev_t-1q) - 1   (quarterly only)
    earnings_growth_yoy     = (ni_t0 / ni_t-4q) - 1
    earnings_growth_qoq     = (ni_t0 / ni_t-1q) - 1
    debt_to_equity          = long_term_debt / total_equity
    fcf_yield               = free_cash_flow / market_cap
                              (market_cap = shares_outstanding * close)
    current_ratio           = current_assets / current_liabilities
    ar_to_revenue           = accounts_receivable / revenue
    cfo_to_ni               = cfo / net_income
    accruals_ratio          = accruals / total_assets
    retained_earnings_ratio = retained_earnings / total_assets
    working_capital_ratio   = working_capital / total_assets
    total_liabilities_ratio = total_liabilities / total_assets
    ebit_margin             = ebit / revenue
    rd_intensity            = rd_expense / revenue
    shares_out              = shares_outstanding (raw passthrough)
    dividend_yield          = -dividends_paid / market_cap
                              (yfinance reports dividends_paid as negative
                              cash outflow; sign-flip to make yield positive)
    buyback_yield           = -buybacks / market_cap (same sign convention)
    asset_turnover          = revenue / total_assets

All values are floats or None. None means inputs were missing or zero
denominator (the ``_safe_div`` guard returns None on div-by-zero rather than
``inf`` / ``nan`` so downstream factor code can branch cleanly).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def _safe_div(num: Any, den: Any) -> float | None:
    """Division guard — returns None on missing inputs or zero denominator.

    Used by every ratio so a single zero-revenue or NULL-equity row does
    not propagate ``inf`` / ``nan`` into the ratios table.
    """
    if num is None or den is None:
        return None
    try:
        n, d = float(num), float(den)
    except (TypeError, ValueError):
        return None
    # NaN check — NaN != NaN
    if n != n or d != d:
        return None
    if d == 0:
        return None
    return n / d


# Output column order for the fundamental_ratios table — matches migration 0002.
_OUTPUT_COLS: list[str] = [
    "roe", "roa", "gross_margin", "operating_margin", "net_margin",
    "revenue_growth_yoy", "revenue_growth_qoq",
    "earnings_growth_yoy", "earnings_growth_qoq",
    "debt_to_equity", "fcf_yield", "current_ratio", "ar_to_revenue",
    "cfo_to_ni", "accruals_ratio", "retained_earnings_ratio",
    "working_capital_ratio", "total_liabilities_ratio",
    "ebit_margin", "rd_intensity", "shares_out",
    "dividend_yield", "buyback_yield", "asset_turnover",
]


def _latest_per_period(
    conn: sqlite3.Connection,
    ticker: str,
    period_type: str,
    asof_str: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return the ``limit`` most recent ``fundamentals`` rows of the given
    period_type, picking the latest ``as_of_ingest_date`` per ``period_end``
    (PIT-aware: never look past ``asof_str``).

    The two-step CTE (newest as_of per period_end, then ORDER BY period_end
    DESC) ensures we get one row per period, picking the most-recently-known
    value as of the asof_str — the D2-correct read pattern.
    """
    sql = """
        WITH latest AS (
            SELECT ticker, period_end, period_type, MAX(as_of_ingest_date) AS aoid
            FROM fundamentals
            WHERE ticker = ?
              AND period_type = ?
              AND as_of_ingest_date <= ?
              AND period_end <= ?
            GROUP BY ticker, period_end, period_type
        )
        SELECT f.* FROM fundamentals f
        JOIN latest l
          ON f.ticker = l.ticker
         AND f.period_end = l.period_end
         AND f.period_type = l.period_type
         AND f.as_of_ingest_date = l.aoid
        ORDER BY f.period_end DESC
        LIMIT ?
    """
    cur = conn.execute(sql, (ticker, period_type, asof_str, asof_str, limit))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def compute_ratios(
    ticker: str, asof: date, conn: sqlite3.Connection
) -> dict[str, float | None]:
    """Return the 24 ratios for ``ticker`` as of ``asof``.

    Reads:
      - up to 5 most-recent quarterly rows (for YoY q0-vs-q4 + QoQ q0-vs-q1)
      - up to 2 most-recent annual rows (fallback if <5 quarters available)
      - close from ``daily_prices`` on/before asof (for market_cap-based yields)

    Output dict keys match ``_OUTPUT_COLS`` exactly.
    """
    asof_str = asof.isoformat()

    quarters = _latest_per_period(conn, ticker, "quarterly", asof_str, 5)
    years = _latest_per_period(conn, ticker, "annual", asof_str, 2)

    base = quarters[0] if quarters else (years[0] if years else None)
    if base is None:
        return {col: None for col in _OUTPUT_COLS}

    # Latest close on or before asof
    close_row = conn.execute(
        "SELECT close FROM daily_prices WHERE ticker=? AND date <= ? "
        "ORDER BY date DESC LIMIT 1",
        (ticker, asof_str),
    ).fetchone()
    close = close_row[0] if close_row else None

    shares_out = base.get("shares_outstanding")
    market_cap: float | None = None
    if close is not None and shares_out is not None:
        try:
            market_cap = float(close) * float(shares_out)
        except (TypeError, ValueError):
            market_cap = None

    # Growth ratios
    rev_yoy: float | None = None
    rev_qoq: float | None = None
    ni_yoy: float | None = None
    ni_qoq: float | None = None
    if len(quarters) >= 5:
        rev_yoy = _safe_div(
            (quarters[0].get("revenue") or 0) - (quarters[4].get("revenue") or 0),
            quarters[4].get("revenue"),
        )
        ni_yoy = _safe_div(
            (quarters[0].get("net_income") or 0) - (quarters[4].get("net_income") or 0),
            quarters[4].get("net_income"),
        )
    elif len(years) >= 2:
        rev_yoy = _safe_div(
            (years[0].get("revenue") or 0) - (years[1].get("revenue") or 0),
            years[1].get("revenue"),
        )
        ni_yoy = _safe_div(
            (years[0].get("net_income") or 0) - (years[1].get("net_income") or 0),
            years[1].get("net_income"),
        )
    if len(quarters) >= 2:
        rev_qoq = _safe_div(
            (quarters[0].get("revenue") or 0) - (quarters[1].get("revenue") or 0),
            quarters[1].get("revenue"),
        )
        ni_qoq = _safe_div(
            (quarters[0].get("net_income") or 0) - (quarters[1].get("net_income") or 0),
            quarters[1].get("net_income"),
        )

    out: dict[str, float | None] = {
        "roe": _safe_div(base.get("net_income"), base.get("total_equity")),
        "roa": _safe_div(base.get("net_income"), base.get("total_assets")),
        "gross_margin": _safe_div(base.get("gross_profit"), base.get("revenue")),
        "operating_margin": _safe_div(base.get("operating_income"), base.get("revenue")),
        "net_margin": _safe_div(base.get("net_income"), base.get("revenue")),
        "revenue_growth_yoy": rev_yoy,
        "revenue_growth_qoq": rev_qoq,
        "earnings_growth_yoy": ni_yoy,
        "earnings_growth_qoq": ni_qoq,
        "debt_to_equity": _safe_div(base.get("long_term_debt"), base.get("total_equity")),
        "fcf_yield": _safe_div(base.get("free_cash_flow"), market_cap),
        "current_ratio": _safe_div(base.get("current_assets"), base.get("current_liabilities")),
        "ar_to_revenue": _safe_div(base.get("accounts_receivable"), base.get("revenue")),
        "cfo_to_ni": _safe_div(base.get("cfo"), base.get("net_income")),
        "accruals_ratio": _safe_div(base.get("accruals"), base.get("total_assets")),
        "retained_earnings_ratio": _safe_div(
            base.get("retained_earnings"), base.get("total_assets")
        ),
        "working_capital_ratio": _safe_div(
            base.get("working_capital"), base.get("total_assets")
        ),
        "total_liabilities_ratio": _safe_div(
            base.get("total_liabilities"), base.get("total_assets")
        ),
        "ebit_margin": _safe_div(base.get("ebit"), base.get("revenue")),
        "rd_intensity": _safe_div(base.get("rd_expense"), base.get("revenue")),
        "shares_out": float(shares_out) if shares_out is not None else None,
        "dividend_yield": (
            _safe_div(-1 * (base.get("dividends_paid") or 0), market_cap)
            if base.get("dividends_paid") is not None
            else None
        ),
        "buyback_yield": (
            _safe_div(-1 * (base.get("buybacks") or 0), market_cap)
            if base.get("buybacks") is not None
            else None
        ),
        "asset_turnover": _safe_div(base.get("revenue"), base.get("total_assets")),
    }
    return out


def compute_all_ratios(conn: sqlite3.Connection, asof: date) -> int:
    """Compute and persist ratios for every active universe ticker.

    INSERT OR REPLACE keyed by (ticker, asof_date) — same-asof reruns
    overwrite (ratios are a derived snapshot, not a historical record;
    the historical record lives in ``fundamentals`` keyed by
    ``as_of_ingest_date``).

    Returns the row count written.
    """
    asof_str = asof.isoformat()
    tickers = [
        r[0] for r in conn.execute(
            "SELECT ticker FROM universe WHERE delisted_date IS NULL ORDER BY ticker"
        )
    ]
    n = 0
    cols = "ticker, asof_date, " + ", ".join(_OUTPUT_COLS)
    placeholders = ", ".join(["?"] * (2 + len(_OUTPUT_COLS)))
    sql = f"INSERT OR REPLACE INTO fundamental_ratios ({cols}) VALUES ({placeholders})"
    for t in tickers:
        ratios = compute_ratios(t, asof, conn)
        values: list[Any] = [t, asof_str] + [ratios.get(c) for c in _OUTPUT_COLS]
        conn.execute(sql, values)
        n += 1
    log.info("ratios_computed", n=n, asof=asof_str)
    return n


__all__ = ["compute_all_ratios", "compute_ratios"]
