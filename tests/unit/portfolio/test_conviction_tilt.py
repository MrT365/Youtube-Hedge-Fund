"""Conviction-tilt optimiser tests (PORT-01)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.conviction_tilt import (
    TILT_TOP5_MULT,
    TILT_TOP10_MULT,
    build_target_book,
    select_candidates,
)


def _candidate_universe(n: int = 60) -> pd.DataFrame:
    """60 names spread evenly across 6 sectors with monotonic scores."""
    sectors = ["Tech", "Health", "Financials", "Consumer", "Energy", "Industrials"]
    rows = []
    for i in range(n):
        rows.append(
            {
                "ticker": f"T{i:02d}",
                "sector": sectors[i % len(sectors)],
                "score": float(100 - i),  # T00 = 100, T01 = 99, ...
                "price": 50.0 + (i % 20),
                "adv_usd": 50_000_000.0,  # plenty of room
                "has_earnings_within_window": False,
                "earnings_date": None,
            }
        )
    return pd.DataFrame(rows)


def _cfg(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "num_longs": 5,
        "num_shorts": 5,
        "gross_target": 1.0,
        "max_position_pct": 1.0,
        "adv_cap_pct": 1.0,
        "max_sector_pct": 1.0,
        "max_beta": 5.0,  # disable beta-adjust for unit isolation
        "earnings_halve_window_days": 5,
        "target_aum_usd": 1_000_000.0,
    }
    base.update(overrides)
    return PortfolioConfig(**base)


def test_select_candidates_picks_top_and_bottom() -> None:
    df = _candidate_universe(60)
    cfg = _cfg(num_longs=5, num_shorts=5)
    longs, shorts = select_candidates(df, cfg=cfg)
    assert len(longs) == 5
    assert len(shorts) == 5
    # Longs are highest scores; shorts are lowest.
    assert longs["score"].min() > shorts["score"].max()


def test_select_candidates_no_overlap() -> None:
    df = _candidate_universe(20)
    cfg = _cfg(num_longs=8, num_shorts=8)
    longs, shorts = select_candidates(df, cfg=cfg)
    overlap = set(longs["ticker"]) & set(shorts["ticker"])
    assert overlap == set()


def test_conviction_tilt_top5_gets_1_5x() -> None:
    """With N=20 longs, top 5% = 1 name → tilt 1.5×. Next 5% = 1 name → 1.25×.

    The tilt is renormalised to preserve ``gross_target`` (PORT-10), so we test
    the *ratio* of the top5/top10/base weights — invariant under renorm.
    """
    df = _candidate_universe(60)
    cfg = _cfg(num_longs=20, num_shorts=20, gross_target=1.0)
    result = build_target_book(df, cfg=cfg, target_aum_usd=1_000_000)
    longs = result.targets[result.targets["side"] == "long"].sort_values("score", ascending=False)
    top5_weight = float(longs["tilted_weight"].iloc[0])
    top10_weight = float(longs["tilted_weight"].iloc[1])
    base_band_weight = float(longs["tilted_weight"].iloc[3])  # well outside top 10%
    assert math.isclose(top5_weight / base_band_weight, TILT_TOP5_MULT, rel_tol=1e-6)
    assert math.isclose(top10_weight / base_band_weight, TILT_TOP10_MULT, rel_tol=1e-6)
    # Bucket labels persisted for audit.
    assert longs["tilt_bucket"].iloc[0] == "top5"
    assert longs["tilt_bucket"].iloc[1] == "top10"
    assert longs["tilt_bucket"].iloc[3] == "base"


def test_conviction_tilt_shorts_have_negative_weight() -> None:
    df = _candidate_universe(60)
    cfg = _cfg(num_longs=10, num_shorts=10)
    result = build_target_book(df, cfg=cfg)
    shorts = result.targets[result.targets["side"] == "short"]
    assert (shorts["final_weight"] < 0).all()


def test_gross_matches_target() -> None:
    """Without ADV cap / beta adjust / earnings halve / sector cap, gross
    should equal cfg.gross_target."""
    df = _candidate_universe(60)
    cfg = _cfg(num_longs=10, num_shorts=10, gross_target=1.5)
    result = build_target_book(df, cfg=cfg)
    assert abs(result.gross - 1.5) < 1e-6


def test_adv_cap_clips_oversized_positions() -> None:
    """Tiny ADV → top-tilt names get clipped to adv_cap × adv."""
    df = _candidate_universe(20)
    df.loc[df["ticker"] == "T00", "adv_usd"] = 100_000  # tiny ADV → 5% = $5,000
    cfg = _cfg(
        num_longs=20,
        num_shorts=20,
        gross_target=1.5,
        adv_cap_pct=0.05,
        target_aum_usd=1_000_000,
        max_position_pct=1.0,  # only ADV constrains
    )
    # equal-weight base = 0.75 / 20 = 0.0375 = $37,500. Tilted top5 = $56,250.
    # ADV cap = 5% × $100k = $5,000. So T00 should land at $5,000 max.
    result = build_target_book(df, cfg=cfg, target_aum_usd=1_000_000)
    t00 = result.targets[result.targets["ticker"] == "T00"]
    if not t00.empty:
        assert t00["target_dollar"].iloc[0] <= 5_000.0 + 1e-6


def test_earnings_halved_position_is_half_size() -> None:
    """A name with earnings within window should be sized to 50% of its
    pre-halve weight."""
    df = _candidate_universe(20)
    df.loc[df["ticker"] == "T05", "has_earnings_within_window"] = True
    cfg = _cfg(num_longs=10, num_shorts=10)
    result = build_target_book(df, cfg=cfg)
    t05 = result.targets[result.targets["ticker"] == "T05"]
    if not t05.empty and t05["side"].iloc[0] == "long":
        assert t05["earnings_halved"].iloc[0] is True or t05["earnings_halved"].iloc[0] == 1
        # Final weight should be half the pre-halve adv_capped weight.
        adv_capped = float(t05["adv_capped_weight"].iloc[0])
        final = float(t05["final_weight"].iloc[0])
        assert math.isclose(final, adv_capped * 0.5, rel_tol=1e-6)


def test_beta_adjust_brings_net_beta_under_cap() -> None:
    """Force a strongly-positive long-side beta and verify the optimiser
    scales longs down to land within max_beta."""
    df = _candidate_universe(20)
    cfg = _cfg(num_longs=10, num_shorts=10, max_beta=0.10)
    # Longs have beta 2.0 (high); shorts have beta 0.1 (low).
    longs_tickers = [f"T{i:02d}" for i in range(0, 10)]
    shorts_tickers = [f"T{i:02d}" for i in range(10, 20)]
    betas = {t: 2.0 for t in longs_tickers}
    betas.update({t: 0.1 for t in shorts_tickers})
    result = build_target_book(df, cfg=cfg, betas=betas)
    assert abs(result.book_beta.net_beta) <= 0.10 + 1e-6


def test_sector_cap_enforced() -> None:
    """If one sector dominates the long book, sector_net is capped at
    max_sector_pct."""
    rows = []
    for i in range(40):
        sector = "Tech" if i < 20 else "Other"
        rows.append(
            {
                "ticker": f"X{i:02d}",
                "sector": sector,
                "score": float(100 - i),
                "price": 100.0,
                "adv_usd": 1_000_000_000.0,
                "has_earnings_within_window": False,
                "earnings_date": None,
            }
        )
    df = pd.DataFrame(rows)
    cfg = _cfg(
        num_longs=10,
        num_shorts=10,
        max_sector_pct=0.20,
        gross_target=1.0,
    )
    result = build_target_book(df, cfg=cfg)
    if result.sector_net:
        for sec, net in result.sector_net.items():
            assert abs(net) <= 0.20 + 1e-6, f"sector {sec} net {net} exceeds cap"


def test_empty_candidates_returns_empty_result() -> None:
    cfg = _cfg()
    result = build_target_book(pd.DataFrame(columns=["ticker", "sector", "score"]), cfg=cfg)
    assert result.targets.empty
    assert result.gross == 0.0


def test_select_candidates_requires_score_column() -> None:
    cfg = _cfg()
    with pytest.raises(ValueError, match="score"):
        select_candidates(pd.DataFrame({"ticker": ["A"]}), cfg=cfg)
