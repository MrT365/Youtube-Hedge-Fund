"""Beta tests (PORT-07)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ls_equity_fund.portfolio.beta import aggregate_book_beta, compute_betas


def _seed_prices(conn: sqlite3.Connection, ticker: str, prices: list[tuple[date, float]]) -> None:
    rows = [
        (ticker, d.isoformat(), float(p), float(p), float(p), float(p), float(p), 1_000_000)
        for d, p in prices
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_prices "
            "(ticker, date, open, high, low, close, adj_close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _trading_days(end: date, n: int) -> list[date]:
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def test_compute_beta_perfect_correlation(conn: sqlite3.Connection) -> None:
    """Stock that moves 1.5x SPY exactly should report beta ≈ 1.5."""
    asof = date(2026, 5, 1)
    days = _trading_days(asof, 100)
    rng = np.random.default_rng(42)
    spy_rets = rng.normal(0, 0.01, size=len(days))
    spy_prices = 100 * np.cumprod(1 + spy_rets)
    stock_rets = 1.5 * spy_rets
    stock_prices = 100 * np.cumprod(1 + stock_rets)
    _seed_prices(conn, "SPY", list(zip(days, spy_prices, strict=False)))
    _seed_prices(conn, "AAA", list(zip(days, stock_prices, strict=False)))

    betas = compute_betas(conn, tickers=["AAA"], asof=asof, lookback=60)
    assert "AAA" in betas
    assert abs(betas["AAA"] - 1.5) < 0.05


def test_compute_beta_zero_for_uncorrelated(conn: sqlite3.Connection) -> None:
    asof = date(2026, 5, 1)
    days = _trading_days(asof, 100)
    rng = np.random.default_rng(0)
    _seed_prices(
        conn,
        "SPY",
        list(zip(days, 100 * np.cumprod(1 + rng.normal(0, 0.01, len(days))), strict=False)),
    )
    _seed_prices(
        conn,
        "BBB",
        list(zip(days, 100 * np.cumprod(1 + rng.normal(0, 0.01, len(days))), strict=False)),
    )
    betas = compute_betas(conn, tickers=["BBB"], asof=asof, lookback=60)
    # Uncorrelated noise → small beta in expectation; just assert it computes a finite value.
    assert "BBB" in betas
    assert abs(betas["BBB"]) < 1.0


def test_compute_beta_short_history_omits(conn: sqlite3.Connection) -> None:
    """Tickers with <20 returns should be omitted from output."""
    asof = date(2026, 5, 1)
    days = _trading_days(asof, 100)
    _seed_prices(conn, "SPY", list(zip(days, np.linspace(100, 110, len(days)), strict=False)))
    _seed_prices(conn, "NEW", list(zip(days[-15:], np.linspace(50, 55, 15), strict=False)))
    betas = compute_betas(conn, tickers=["NEW"], asof=asof, lookback=60)
    assert "NEW" not in betas


def test_aggregate_book_beta_long_only() -> None:
    weights = pd.Series({"AAA": 0.04, "BBB": 0.04})
    betas = {"AAA": 1.0, "BBB": 1.5}
    book = aggregate_book_beta(weights=weights, betas=betas)
    # Long book weighted-avg = (0.04*1 + 0.04*1.5) / 0.08 = 1.25
    assert abs(book.long_book_beta - 1.25) < 1e-6
    # Net = signed weighted sum = 0.04 + 0.06 = 0.10
    assert abs(book.net_beta - 0.10) < 1e-6
    assert book.short_book_beta == 0.0
    assert book.n_long == 2 and book.n_short == 0


def test_aggregate_book_beta_market_neutral() -> None:
    """Equal-and-opposite betas → net beta ≈ 0."""
    weights = pd.Series({"L1": 0.05, "L2": 0.05, "S1": -0.05, "S2": -0.05})
    betas = {"L1": 1.0, "L2": 1.0, "S1": 1.0, "S2": 1.0}
    book = aggregate_book_beta(weights=weights, betas=betas)
    assert abs(book.net_beta) < 1e-9
    assert book.long_book_beta == 1.0
    assert book.short_book_beta == 1.0
    assert book.n_long == 2
    assert book.n_short == 2


def test_aggregate_handles_missing_betas() -> None:
    weights = pd.Series({"AAA": 0.05, "BBB": 0.05})
    book = aggregate_book_beta(weights=weights, betas={"AAA": 1.2})
    # BBB's beta is missing → contributes 0 to numerator; both names contribute to gross.
    # Long-book beta = (0.05 * 1.2 + 0.05 * 0) / 0.10 = 0.6
    assert abs(book.long_book_beta - 0.6) < 1e-9
    # Net beta sums signed-weight × beta only over tickers with known betas: 0.05 * 1.2 = 0.06
    assert abs(book.net_beta - 0.05 * 1.2) < 1e-9
