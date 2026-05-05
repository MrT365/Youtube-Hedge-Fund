"""MVO optimizer tests (Phase 7 PORT-02 / PORT-03)."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.mvo import (
    FALLBACK_USED,
    MVOFailure,
    build_mvo_or_fallback,
    build_mvo_target_book,
)


def _candidates(n: int = 40) -> pd.DataFrame:
    sectors = ["Tech", "Health", "Financials", "Consumer"]
    rows = []
    for i in range(n):
        rows.append(
            {
                "ticker": f"T{i:02d}",
                "sector": sectors[i % len(sectors)],
                "score": 100 - i * (100 / max(n - 1, 1)),
                "price": 100.0,
                "adv_usd": 100_000_000.0,
                "has_earnings_within_window": False,
            }
        )
    return pd.DataFrame(rows)


def _cov(candidates: pd.DataFrame, diag: float = 0.10) -> pd.DataFrame:
    tickers = candidates["ticker"].tolist()
    return pd.DataFrame(np.eye(len(tickers)) * diag, index=tickers, columns=tickers)


def test_mvo_produces_valid_20l_20s_weights() -> None:
    cfg = PortfolioConfig()
    candidates = _candidates()
    result = build_mvo_target_book(
        candidates,
        cfg=cfg,
        covariance=_cov(candidates, diag=0.10),
        betas={ticker: 0.0 for ticker in candidates["ticker"]},
    )

    targets = result.targets
    assert int((targets["side"] == "long").sum()) == 20
    assert int((targets["side"] == "short").sum()) == 20
    assert targets["final_weight"].abs().max() <= cfg.max_position_pct + 1e-8
    assert result.long_gross == pytest.approx(cfg.gross_target / 2, abs=1e-5)
    assert result.short_gross == pytest.approx(cfg.gross_target / 2, abs=1e-5)
    assert abs(result.book_beta.net_beta) <= 0.20
    assert max(abs(v) for v in result.sector_net.values()) <= cfg.max_sector_pct + 1e-6
    assert result.annualized_vol >= 0.05


def test_vol_sanity_check_falls_back_and_audits(conn: sqlite3.Connection) -> None:
    cfg = PortfolioConfig()
    candidates = _candidates()
    result = build_mvo_or_fallback(
        conn,
        candidates,
        cfg=cfg,
        covariance=_cov(candidates, diag=0.000001),
        betas={ticker: 0.0 for ticker in candidates["ticker"]},
    )

    assert result.used_fallback is True
    assert result.fallback_reason is not None
    assert "vol_sanity_check_failed" in result.fallback_reason
    row = conn.execute(
        "SELECT reason, fallback_used FROM optimizer_fallback_log"
    ).fetchone()
    assert row[1] == FALLBACK_USED
    assert "vol_sanity_check_failed" in row[0]


def test_non_convergence_falls_back_and_audits(conn: sqlite3.Connection) -> None:
    cfg = PortfolioConfig(max_position_pct=0.01)
    candidates = _candidates()
    result = build_mvo_or_fallback(
        conn,
        candidates,
        cfg=cfg,
        covariance=_cov(candidates, diag=0.10),
        betas={ticker: 0.0 for ticker in candidates["ticker"]},
    )

    assert result.used_fallback is True
    row = conn.execute(
        "SELECT reason, fallback_used FROM optimizer_fallback_log"
    ).fetchone()
    assert row[1] == FALLBACK_USED


def test_optimizer_fallback_log_is_immutable(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO optimizer_fallback_log (
            timestamp, reason, fallback_used, portfolio_state_json
        ) VALUES (?, ?, ?, ?)
        """,
        (1, "mvo_non_convergence:test", FALLBACK_USED, "{}"),
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE optimizer_fallback_log SET reason = 'changed'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM optimizer_fallback_log")


def test_fallback_never_reuses_yesterday_weights(conn: sqlite3.Connection) -> None:
    cfg = PortfolioConfig()
    candidates = _candidates()
    result = build_mvo_or_fallback(
        conn,
        candidates,
        cfg=cfg,
        covariance=_cov(candidates, diag=0.000001),
        betas={ticker: 0.0 for ticker in candidates["ticker"]},
    )
    assert result.used_fallback is True
    assert not result.targets.empty
    assert "yesterday" not in (result.fallback_reason or "")


def test_direct_mvo_raises_on_missing_covariance() -> None:
    with pytest.raises(MVOFailure):
        build_mvo_target_book(
            _candidates(),
            cfg=PortfolioConfig(),
            covariance=pd.DataFrame(),
            betas={},
        )
