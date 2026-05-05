from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ls_equity_fund.config import TaxConfig
from ls_equity_fund.reporting.commentary import (
    generate_weekly_commentary,
    should_generate_commentary,
)
from ls_equity_fund.reporting.daily_letter import MANDATORY_DISCLAIMER, generate_daily_letter
from ls_equity_fund.reporting.pnl_attribution import decompose_daily_return
from ls_equity_fund.reporting.position_attribution import (
    fifo_round_trips,
    spearman_signal_quality,
)
from ls_equity_fund.reporting.tear_sheet import (
    NAMED_METRICS,
    monthly_returns_grid,
    write_tear_sheet,
)
from ls_equity_fund.reporting.turnover import tax_estimate
from ls_equity_fund.reporting.win_loss import sector_relative_alpha, vix_regime, win_loss_slices


@dataclass
class FakeResponse:
    text: str


class FakeClient:
    model = "fake-claude"

    def __init__(self) -> None:
        self.calls = 0

    def call(self, **kwargs: object) -> FakeResponse:
        self.calls += 1
        return FakeResponse(f"fake body {self.calls}")


def test_pnl_attribution_components_sum_to_daily_return() -> None:
    parts = decompose_daily_return(
        daily_return=0.012,
        net_beta=0.5,
        spy_return=0.01,
        sector_return=0.002,
        factor_return=0.001,
    )
    assert parts["beta_return"] + parts["sector_return"] + parts["factor_return"] + parts["alpha_return"] == pytest.approx(parts["daily_return"])


def test_fifo_round_trips_and_spearman_known_data() -> None:
    trades = pd.DataFrame(
        [
            {"date": "2026-01-01", "ticker": "AAA", "side": "long", "shares": 10, "price": 10, "entry_score": 90, "sector": "Tech", "vix_at_entry": 14, "factor_quintile_at_entry": 5},
            {"date": "2026-01-02", "ticker": "AAA", "side": "long", "shares": 5, "price": 12, "entry_score": 50, "sector": "Tech", "vix_at_entry": 20, "factor_quintile_at_entry": 3},
            {"date": "2026-01-10", "ticker": "AAA", "side": "short", "shares": -12, "price": 15},
            {"date": "2026-02-01", "ticker": "BBB", "side": "long", "shares": 10, "price": 10, "entry_score": 10, "sector": "Health", "vix_at_entry": 36, "factor_quintile_at_entry": 1},
            {"date": "2026-02-10", "ticker": "BBB", "side": "short", "shares": -10, "price": 9},
        ]
    )
    trips = fifo_round_trips(trades)
    assert len(trips) == 3
    assert trips[0].shares == 10
    assert trips[1].shares == 2
    assert spearman_signal_quality(trips) > 0


def test_win_loss_buckets_sum_and_vix_boundaries() -> None:
    assert vix_regime(14.99) == "CALM"
    assert vix_regime(15.0) == "NORMAL"
    assert vix_regime(25.0) == "ELEVATED"
    assert vix_regime(35.0) == "CRISIS"
    trips = pd.DataFrame(
        {
            "side": ["long", "short", "long"],
            "holding_bucket": ["1-5d", "5-20d", "1-5d"],
            "sector": ["Tech", "Tech", "Health"],
            "vix_at_entry": [10, 20, 40],
            "factor_quintile_at_entry": [5, 1, 3],
            "realized_pnl": [1.0, -1.0, 2.0],
        }
    )
    slices = win_loss_slices(trips)
    assert int(slices["side"]["trades"].sum()) == len(trips)
    assert int(slices["holding_bucket"]["trades"].sum()) == len(trips)


def test_sector_relative_alpha_and_tax_config_rates() -> None:
    alpha = sector_relative_alpha(
        pd.DataFrame({"sector": ["Tech"], "pick_return": [0.10], "sector_etf_return": [0.04]})
    )
    assert alpha.loc[0, "selection_alpha"] == pytest.approx(0.06)
    tax = tax_estimate(
        pd.DataFrame({"realized_pnl": [100.0, 100.0], "holding_days": [10, 400]}),
        TaxConfig(jurisdiction_name="Test", short_term_rate=0.10, long_term_rate=0.01),
    )
    assert tax == pytest.approx(11.0)


def test_tear_sheet_named_metrics_and_monthly_grid(conn: sqlite3.Connection, tmp_path: Path) -> None:
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-02-02"])
    returns = pd.Series([0.01, -0.005, 0.02], index=idx)
    spy = pd.Series([0.005, -0.002, 0.01], index=idx)
    path = write_tear_sheet(
        conn,
        run_id="r",
        asof_date="2026-02-02",
        returns=returns,
        spy_returns=spy,
        trade_pnls=pd.Series([1.0, -0.5]),
        risk_free_rate=0.0,
        output_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    for metric in NAMED_METRICS:
        assert metric in text
    grid = monthly_returns_grid(returns)
    assert {1, 2}.issubset(set(grid.columns))


def test_daily_letter_fields_modes_cache_and_regenerate(conn: sqlite3.Connection) -> None:
    client = FakeClient()
    day = date(2026, 5, 1)
    lp = generate_daily_letter(conn, day=day, mode="lp", client=client, domicile="Delaware", fund_aum_usd=123, regenerate=False)
    assert "MCP-IM-2026-0501" in lp
    assert "CONFIDENTIAL" in lp
    assert "Delaware" in lp
    assert "Dear Limited Partners" in lp
    assert "Compliance footer" in lp
    assert MANDATORY_DISCLAIMER in lp
    assert "PAPER" in lp
    internal = generate_daily_letter(conn, day=day, mode="internal", client=client, regenerate=False)
    assert internal != lp
    again = generate_daily_letter(conn, day=day, mode="lp", client=client, regenerate=False)
    assert again == lp
    assert client.calls == 2
    changed = generate_daily_letter(conn, day=day, mode="lp", client=client, regenerate=True)
    assert changed != lp
    assert client.calls == 3


def test_commentary_weekday_and_cache(conn: sqlite3.Connection) -> None:
    client = FakeClient()
    friday = date(2026, 5, 1)
    assert should_generate_commentary(friday, weekday=4)
    assert not should_generate_commentary(date(2026, 5, 4), weekday=4)
    first = generate_weekly_commentary(conn, week_ending=friday, client=client)
    second = generate_weekly_commentary(conn, week_ending=friday, client=client)
    assert first == second
    assert client.calls == 1
