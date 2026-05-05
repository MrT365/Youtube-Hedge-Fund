"""Pre-trade veto tests."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd
import pytest

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.risk.pre_trade_veto import (
    TradeRequest,
    VetoContext,
    evaluate_pre_trade_veto,
    is_closing_trade,
)


@pytest.fixture
def cfg() -> PortfolioConfig:
    return PortfolioConfig()


def _positions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["MSFT"],
            "side": ["long"],
            "shares": [100.0],
            "entry_price": [100.0],
            "current_price": [100.0],
            "sector": ["Tech"],
        }
    )


def _base_trade(**overrides: object) -> TradeRequest:
    data = {
        "ticker": "AAPL",
        "side": "long",
        "shares": 10.0,
        "price": 100.0,
        "sector": "Health",
        "beta": 0.0,
        "adv_20d_usd": 1_000_000.0,
    }
    data.update(overrides)
    return TradeRequest(**data)


def _base_context(**overrides: object) -> VetoContext:
    data = {
        "aum_usd": 1_000_000.0,
        "current_positions": pd.DataFrame(),
        "asof": date(2026, 5, 4),
        "max_net_beta": 10.0,
    }
    data.update(overrides)
    return VetoContext(**data)


def test_closing_trade_cp5_definition() -> None:
    assert is_closing_trade(100, -25) is True
    assert is_closing_trade(-100, 25) is True
    assert is_closing_trade(100, -100) is False  # zero is not sign-preserving
    assert is_closing_trade(100, -150) is False  # flip/reverse
    assert is_closing_trade(100, 25) is False  # increases
    assert is_closing_trade(0, -25) is False


def test_pass_case(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(),
        context=_base_context(),
        portfolio_cfg=cfg,
    )
    assert result.accepted is True
    assert result.reasons == []


def test_halt_lock_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(),
        context=_base_context(halted_tickers={"AAPL"}),
        portfolio_cfg=cfg,
    )
    assert "halt_lock" in result.reasons


def test_earnings_blackout_new_entries_only(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    asof = date(2026, 5, 4)
    context = _base_context(earnings_dates={"AAPL": asof + timedelta(days=1)}, asof=asof)
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(is_new_entry=True),
        context=context,
        portfolio_cfg=cfg,
    )
    assert "earnings_blackout" in result.reasons


def test_earnings_blackout_closing_exempt(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    asof = date(2026, 5, 4)
    positions = pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "side": ["long"],
            "shares": [100.0],
            "entry_price": [100.0],
            "current_price": [100.0],
            "sector": ["Tech"],
        }
    )
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(shares=-25.0, sector="Tech"),
        context=_base_context(
            current_positions=positions,
            earnings_dates={"AAPL": asof + timedelta(days=1)},
            asof=asof,
        ),
        portfolio_cfg=cfg,
    )
    assert "earnings_blackout" not in result.reasons
    assert result.is_closing_trade is True


def test_liquidity_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(shares=1_000, adv_20d_usd=1_000_000.0),
        context=_base_context(),
        portfolio_cfg=cfg,
    )
    assert "liquidity_gt_5pct_adv" in result.reasons


def test_position_size_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(shares=1_000),
        context=_base_context(),
        portfolio_cfg=cfg,
    )
    assert "position_size_gt_5pct_aum" in result.reasons


def test_sector_concentration_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(shares=2_600, sector="Tech"),
        context=_base_context(current_positions=_positions(), max_net_beta=10.0),
        portfolio_cfg=cfg,
    )
    assert "sector_concentration_gt_25pct" in result.reasons


def test_gross_net_exposure_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(shares=20_000, adv_20d_usd=100_000_000),
        context=_base_context(max_net_beta=10.0),
        portfolio_cfg=cfg,
    )
    assert "gross_net_exposure_out_of_bounds" in result.reasons


def test_beta_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(shares=1_000, adv_20d_usd=100_000_000, beta=3.0),
        context=_base_context(max_net_beta=0.20),
        portfolio_cfg=cfg,
    )
    assert "net_beta_gt_0.20" in result.reasons


def test_correlation_fail(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    corr = pd.DataFrame([[1.0, 0.9], [0.9, 1.0]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"])
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(),
        context=_base_context(current_positions=_positions(), correlations=corr),
        portfolio_cfg=cfg,
    )
    assert "pairwise_correlation_gt_0.80" in result.reasons


def test_rejection_persists_veto_log(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(),
        context=_base_context(halted_tickers={"AAPL"}),
        portfolio_cfg=cfg,
    )
    row = migrated_conn.execute("SELECT ticker, reason FROM veto_log").fetchone()
    assert row == ("AAPL", "halt_lock")


def test_claimed_closing_label_cannot_bypass(migrated_conn: sqlite3.Connection, cfg: PortfolioConfig) -> None:
    result = evaluate_pre_trade_veto(
        migrated_conn,
        trade=_base_trade(claimed_closing=True, is_new_entry=True),
        context=_base_context(earnings_dates={"AAPL": date(2026, 5, 5)}, asof=date(2026, 5, 4)),
        portfolio_cfg=cfg,
    )
    assert result.is_closing_trade is False
    assert "earnings_blackout" in result.reasons
