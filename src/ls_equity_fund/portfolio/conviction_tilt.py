"""Conviction-tilt optimizer (PORT-01).

Always-works optimiser that ships in Phase 5 ahead of MVO (Phase 7). The MVO
seam (`mvo.py`) raises NotImplementedError until Phase 7 lights it up.

Algorithm:

  1. Pick top N longs and top N shorts (default 20 / 20) by combined parent
     score, sector-balanced (no sector dominates either book beyond
     ``max_sector_pct``).
  2. Equal-weight base sizing within each side: ``gross_target/2 / N``.
  3. Tilt by score conviction:
       * top 5% of side by score → 1.5x base
       * next 5% (i.e. ranks 5–10%) → 1.25x base
       * everyone else → 1.0x base
  4. ADV cap: any single position cannot exceed ``adv_cap_pct`` of the
     trailing 20-day ADV (default 5%). If a tilt pushes a name above that cap,
     it gets clipped down and the freed weight is redistributed to non-capped
     names within the same side, preserving gross.
  5. Earnings halve: if the candidate has an earnings event within
     ``earnings_halve_window_days`` of the asof date, halve the size and let
     the freed weight fall to the side's reserve (we deliberately do NOT
     redistribute — earnings risk is the reason we sized down).
  6. Beta-adjust: scale long and short books so the resulting net beta is
     <= ``max_beta``. Done by scaling the smaller-beta side up (or the
     larger-beta side down) within the per-position cap until net |beta|
     fits.
  7. Sector-neutralise (best-effort): re-balance long-side and short-side
     sector weights so the sector-net (long_sector_weight − short_sector_weight)
     stays inside ``max_sector_pct`` per sector. We do not enforce per-sector
     gross equality — that's a Phase 7 MVO feature — but we cap any one
     sector's net contribution.

Returns a DataFrame indexed by ticker with the full audit trail (base /
tilted / adv_capped / earnings_halved / beta_adjusted / final) so the
``position_approvals`` table gets every column ROADMAP SC1 + SC2 require.

This module ignores the ``cov`` arg from the Optimizer ABC (Phase 5 only ships
conviction-tilt).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import timedelta

import numpy as np
import pandas as pd
import structlog

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.base import Optimizer
from ls_equity_fund.portfolio.beta import (
    DEFAULT_BETA_LOOKBACK,
    BookBeta,
    aggregate_book_beta,
    compute_betas,
)

log = structlog.get_logger(__name__)

TILT_TOP5_MULT = 1.5
TILT_TOP10_MULT = 1.25
TILT_BASE_MULT = 1.0


@dataclass
class ConvictionTiltResult:
    """Full output of the conviction-tilt optimiser."""

    targets: pd.DataFrame  # row per ticker; see columns in build()
    book_beta: BookBeta
    gross: float
    net: float
    long_gross: float
    short_gross: float
    sector_net: dict[str, float]
    flags: list[str] = field(default_factory=list)


class ConvictionTiltOptimizer(Optimizer):
    """Phase 5's always-works optimiser. Ignores ``cov``.

    Heavy lifting lives on the helper functions below; the class is a thin
    Optimizer-ABC adapter so Phase 7's MVO swap-in is a one-line config flip.
    """

    def __init__(self, cfg: PortfolioConfig) -> None:
        self._cfg = cfg

    def optimize(
        self,
        candidates: pd.DataFrame,
        cov: object | None,
        constraints: object,
    ) -> pd.DataFrame:
        """ABC entry point — kept for Phase 7 plug-compat.

        Most callers should use :func:`build_target_book` directly because
        it returns the richer ``ConvictionTiltResult`` (audit columns) instead
        of just target weights.
        """
        if not isinstance(constraints, dict):
            msg = "constraints must be a dict produced by build_target_book"
            raise TypeError(msg)
        result = build_target_book(candidates, cfg=self._cfg, **constraints)
        return result.targets


# -----------------------------------------------------------------------------
# Candidate selection
# -----------------------------------------------------------------------------


def select_candidates(
    candidates: pd.DataFrame,
    *,
    cfg: PortfolioConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select longs (top scores) and shorts (bottom scores) sector-balanced.

    Expects ``candidates`` with columns: ticker, sector, score (combined
    percentile_rank ∈ [0, 100]), adv_usd, price, has_earnings_within_window
    (bool), earnings_date (optional). Missing optional columns are filled
    with safe defaults.

    Sector-balance heuristic: no single sector contributes more than
    ``ceil(N / 4)`` names to either book — soft cap; the full sector-neutrality
    happens later in :func:`_sector_balance`.
    """
    if "score" not in candidates.columns:
        msg = "candidates must include a 'score' column (combined percentile)"
        raise ValueError(msg)
    df = candidates.copy()
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["score"])
    if df.empty:
        return df, df

    df["adv_usd"] = pd.to_numeric(df.get("adv_usd"), errors="coerce")
    df["price"] = pd.to_numeric(df.get("price"), errors="coerce")
    df["sector"] = df.get("sector", "Unknown").fillna("Unknown")
    df["has_earnings_within_window"] = (
        df.get("has_earnings_within_window", pd.Series(False, index=df.index))
        .fillna(False)
        .astype(bool)
    )

    df_long = df.sort_values("score", ascending=False).reset_index(drop=True)
    df_short = df.sort_values("score", ascending=True).reset_index(drop=True)

    longs = _sector_balanced_topn(df_long, n=cfg.num_longs)
    shorts = _sector_balanced_topn(df_short, n=cfg.num_shorts)

    # Avoid the same ticker appearing on both sides — keep it on whichever
    # side has the bigger score conviction.
    overlap = set(longs["ticker"]) & set(shorts["ticker"])
    for tk in overlap:
        long_score = float(longs.loc[longs["ticker"] == tk, "score"].iloc[0])
        short_score = float(shorts.loc[shorts["ticker"] == tk, "score"].iloc[0])
        if (100.0 - short_score) > long_score:
            longs = longs[longs["ticker"] != tk]
        else:
            shorts = shorts[shorts["ticker"] != tk]
    longs = longs.head(cfg.num_longs).reset_index(drop=True)
    shorts = shorts.head(cfg.num_shorts).reset_index(drop=True)
    return longs, shorts


def _sector_balanced_topn(df: pd.DataFrame, *, n: int) -> pd.DataFrame:
    """Pick top-N rows but soft-cap any one sector at ceil(n/4)."""
    if df.empty or n <= 0:
        return df.head(0)
    cap = max(1, (n + 3) // 4)  # ceil(n/4)
    counts: dict[str, int] = {}
    out_idx: list[int] = []
    for idx in df.index:
        sec = df.at[idx, "sector"]
        if counts.get(sec, 0) >= cap:
            continue
        counts[sec] = counts.get(sec, 0) + 1
        out_idx.append(idx)
        if len(out_idx) >= n:
            break
    # If sector cap left us short, top up with whatever is left in score order.
    if len(out_idx) < n:
        remaining = [i for i in df.index if i not in set(out_idx)][: n - len(out_idx)]
        out_idx.extend(remaining)
    return df.loc[out_idx].head(n).reset_index(drop=True)


# -----------------------------------------------------------------------------
# Sizing primitives
# -----------------------------------------------------------------------------


def _bucket_for_rank(rank_within_side: int, n: int) -> tuple[str, float]:
    """Return ``(bucket_name, multiplier)`` for index ``rank_within_side`` in
    a side of size ``n``. Rank 0 = highest conviction.

    Top 5% (at least one name) → 1.5x.
    Names ranked 5%–10% → 1.25x. Rest → 1.0x.
    """
    top5_count = max(1, round(0.05 * n))
    top10_count = max(top5_count + 1, round(0.10 * n))
    if rank_within_side < top5_count:
        return "top5", TILT_TOP5_MULT
    if rank_within_side < top10_count:
        return "top10", TILT_TOP10_MULT
    return "base", TILT_BASE_MULT


def _apply_conviction_tilt(side_df: pd.DataFrame, *, base_weight: float) -> pd.DataFrame:
    """Add ``tilt_bucket`` and ``tilted_weight`` columns to ``side_df``.

    side_df is already sorted with the highest-conviction name first.
    base_weight is the equal-weight before tilt. After applying multipliers
    we renormalise back to the side's pre-tilt gross so the book respects
    ``gross_target`` from config (PORT-10).
    """
    n = len(side_df)
    if n == 0:
        return side_df.assign(tilt_bucket="base", tilted_weight=0.0, base_weight=base_weight)
    buckets, mults = zip(*(_bucket_for_rank(i, n) for i in range(n)), strict=False)
    pre_gross = abs(base_weight) * n
    raw = base_weight * np.array(mults)
    raw_gross = float(np.abs(raw).sum())
    scale = pre_gross / raw_gross if raw_gross > 0 else 1.0
    side_df = side_df.copy()
    side_df["tilt_bucket"] = list(buckets)
    side_df["base_weight"] = base_weight
    side_df["tilted_weight"] = raw * scale
    return side_df


def _apply_adv_cap(
    side_df: pd.DataFrame, *, max_position_pct: float, adv_cap_pct: float, target_aum_usd: float
) -> pd.DataFrame:
    """Clip any name that would exceed ADV-cap or per-position cap.

    Capacity per ticker (in USD) = min(max_position_pct * AUM, adv_cap_pct * adv_usd).
    Capacity in fraction of gross = capacity_usd / target_aum_usd.

    Freed weight is redistributed proportionally across non-capped names within
    the same side so the side's gross is preserved (PORT-01 sector-neutral
    intent — see step 4 in the module docstring).
    """
    if side_df.empty:
        return side_df.assign(adv_capped_weight=side_df.get("tilted_weight", 0.0))

    df = side_df.copy()
    df["adv_capped_weight"] = df["tilted_weight"]

    # Convert tilted_weight (fraction of AUM) to USD.
    df["target_dollar_pre_adv"] = df["tilted_weight"].abs() * target_aum_usd
    df["adv_capped_dollar"] = df["target_dollar_pre_adv"].copy()

    cap_usd = []
    for _, row in df.iterrows():
        adv = row.get("adv_usd")
        if adv is None or pd.isna(adv) or adv <= 0:
            adv_limit = float("inf")
        else:
            adv_limit = adv_cap_pct * float(adv)
        pos_limit = max_position_pct * target_aum_usd
        cap_usd.append(min(adv_limit, pos_limit))
    df["cap_usd"] = cap_usd
    df["adv_capped_dollar"] = np.minimum(df["target_dollar_pre_adv"], df["cap_usd"])

    sign = 1.0 if (df["tilted_weight"].sum() >= 0) else -1.0
    df["adv_capped_weight"] = sign * (df["adv_capped_dollar"] / target_aum_usd)

    # Redistribute freed weight to non-capped names so the side gross
    # equals the pre-cap gross (best-effort).
    pre_gross = df["tilted_weight"].abs().sum()
    post_gross = df["adv_capped_weight"].abs().sum()
    freed = pre_gross - post_gross
    if freed > 1e-9:
        non_capped = df[df["adv_capped_dollar"] < df["cap_usd"] - 1e-6]
        if not non_capped.empty:
            headroom_usd = (non_capped["cap_usd"] - non_capped["adv_capped_dollar"]).sum()
            redistribute_usd = min(freed * target_aum_usd, headroom_usd)
            if headroom_usd > 0 and redistribute_usd > 0:
                non_capped_idx = non_capped.index
                shares = (
                    df.loc[non_capped_idx, "cap_usd"] - df.loc[non_capped_idx, "adv_capped_dollar"]
                ) / headroom_usd
                df.loc[non_capped_idx, "adv_capped_dollar"] = (
                    df.loc[non_capped_idx, "adv_capped_dollar"] + shares * redistribute_usd
                )
                df["adv_capped_weight"] = sign * (df["adv_capped_dollar"] / target_aum_usd)
    return df.drop(columns=["target_dollar_pre_adv", "cap_usd", "adv_capped_dollar"])


def _apply_earnings_halve(side_df: pd.DataFrame) -> pd.DataFrame:
    """Halve any name with earnings inside the configured window.

    The freed weight does NOT get redistributed — earnings risk is the reason
    we sized down, so reducing book gross is the desired outcome.
    """
    if side_df.empty:
        return side_df.assign(earnings_halved=False, earnings_halved_weight=0.0)
    df = side_df.copy()
    df["earnings_halved"] = df["has_earnings_within_window"].astype(bool)
    df["earnings_halved_weight"] = np.where(
        df["earnings_halved"], df["adv_capped_weight"] * 0.5, df["adv_capped_weight"]
    )
    return df


def _apply_beta_adjust(
    longs: pd.DataFrame,
    shorts: pd.DataFrame,
    *,
    betas: dict[str, float],
    max_beta: float,
) -> tuple[pd.DataFrame, pd.DataFrame, BookBeta]:
    """Scale either side until |net beta| <= ``max_beta``.

    Strategy: compute the current net beta. If |net_beta| > max_beta, scale
    the side with the larger absolute beta contribution down by the factor
    needed. Updates ``beta_adjusted_weight`` on each side.
    """
    longs = longs.copy()
    shorts = shorts.copy()
    longs["beta_adjusted_weight"] = longs.get("earnings_halved_weight", 0.0)
    shorts["beta_adjusted_weight"] = shorts.get("earnings_halved_weight", 0.0)

    combined = pd.concat(
        [
            longs[["ticker", "beta_adjusted_weight"]].rename(columns={"beta_adjusted_weight": "w"}),
            shorts[["ticker", "beta_adjusted_weight"]].rename(
                columns={"beta_adjusted_weight": "w"}
            ),
        ],
        ignore_index=True,
    ).set_index("ticker")["w"]
    book_beta = aggregate_book_beta(weights=combined, betas=betas)

    if abs(book_beta.net_beta) <= max_beta or abs(book_beta.net_beta) < 1e-9:
        return longs, shorts, book_beta

    # We need |net_beta| <= max_beta. Conservative approach: scale down the
    # offending side's weights uniformly by factor k until net_beta lands at
    # ±max_beta with the same sign as before.
    target = max_beta if book_beta.net_beta > 0 else -max_beta
    long_w = longs.set_index("ticker")["beta_adjusted_weight"]
    short_w = shorts.set_index("ticker")["beta_adjusted_weight"]
    long_beta_dot = float(sum(betas.get(t, 0.0) * w for t, w in long_w.items()))
    short_beta_dot = float(sum(betas.get(t, 0.0) * w for t, w in short_w.items()))

    if book_beta.net_beta > 0:
        # too long-beta — scale longs down OR scale shorts up. Choose scale-down
        # to avoid breaching per-position caps further upstream.
        if long_beta_dot != 0:
            k = (target - short_beta_dot) / long_beta_dot
            k = max(0.0, min(1.0, k))
            longs["beta_adjusted_weight"] = long_w.values * k
    else:
        if short_beta_dot != 0:
            k = (target - long_beta_dot) / short_beta_dot
            k = max(0.0, min(1.0, k))
            shorts["beta_adjusted_weight"] = short_w.values * k

    combined = pd.concat(
        [
            longs[["ticker", "beta_adjusted_weight"]].rename(columns={"beta_adjusted_weight": "w"}),
            shorts[["ticker", "beta_adjusted_weight"]].rename(
                columns={"beta_adjusted_weight": "w"}
            ),
        ],
        ignore_index=True,
    ).set_index("ticker")["w"]
    book_beta = aggregate_book_beta(weights=combined, betas=betas)
    return longs, shorts, book_beta


def _apply_sector_cap(
    longs: pd.DataFrame,
    shorts: pd.DataFrame,
    *,
    max_sector_pct: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    """Cap any one sector's NET (long − |short|) contribution.

    If a sector exceeds ``max_sector_pct`` net, scale that sector's positions
    on the offending side proportionally down. We do NOT add new tickers — the
    candidate set is fixed by upstream selection.

    Sector-neutrality intent: a perfectly hedged book has sector_net ≈ 0. The
    conviction-tilt heuristic is "no sector imbalance > max_sector_pct".
    """
    longs = longs.copy()
    shorts = shorts.copy()
    long_w = longs.assign(w=longs["beta_adjusted_weight"]).groupby("sector")["w"].sum()
    short_w = shorts.assign(w=shorts["beta_adjusted_weight"]).groupby("sector")["w"].sum()
    sector_net = (long_w - short_w.abs()).reindex(
        sorted(set(long_w.index).union(short_w.index)), fill_value=0.0
    )

    # Apply sector net cap.
    for sec, net in sector_net.items():
        if abs(net) <= max_sector_pct:
            continue
        # net positive → too long this sector → scale longs in sector down
        if net > 0:
            mask = longs["sector"] == sec
            current = float(longs.loc[mask, "beta_adjusted_weight"].sum())
            allowed = max_sector_pct + (current - net)  # so that long-short = max_sector_pct
            if current > 0:
                k = max(0.0, min(1.0, allowed / current))
                longs.loc[mask, "beta_adjusted_weight"] = (
                    longs.loc[mask, "beta_adjusted_weight"] * k
                )
        else:
            mask = shorts["sector"] == sec
            current = float(shorts.loc[mask, "beta_adjusted_weight"].abs().sum())
            allowed = max_sector_pct + (current - abs(net))
            if current > 0:
                k = max(0.0, min(1.0, allowed / current))
                shorts.loc[mask, "beta_adjusted_weight"] = (
                    shorts.loc[mask, "beta_adjusted_weight"] * k
                )

    long_w = longs.assign(w=longs["beta_adjusted_weight"]).groupby("sector")["w"].sum()
    short_w = shorts.assign(w=shorts["beta_adjusted_weight"]).groupby("sector")["w"].sum()
    sector_net = (long_w - short_w.abs()).reindex(
        sorted(set(long_w.index).union(short_w.index)), fill_value=0.0
    )
    return longs, shorts, sector_net.to_dict()


# -----------------------------------------------------------------------------
# Top-level entry
# -----------------------------------------------------------------------------


def build_target_book(
    candidates: pd.DataFrame,
    *,
    cfg: PortfolioConfig,
    betas: dict[str, float] | None = None,
    target_aum_usd: float | None = None,
) -> ConvictionTiltResult:
    """Construct the 20L/20S target book per PORT-01.

    ``candidates`` must contain: ticker, sector, score, price, adv_usd,
    has_earnings_within_window. ``betas`` is optional (missing names assumed
    beta=0 in book aggregation, which is permissive for the v1 path; Phase 6
    risk veto enforces a hard limit).
    """
    if candidates.empty:
        return ConvictionTiltResult(
            targets=pd.DataFrame(),
            book_beta=BookBeta(0.0, 0.0, 0.0, 0, 0),
            gross=0.0,
            net=0.0,
            long_gross=0.0,
            short_gross=0.0,
            sector_net={},
        )
    target_aum_usd = target_aum_usd or cfg.target_aum_usd
    betas = betas or {}

    longs, shorts = select_candidates(candidates, cfg=cfg)
    if longs.empty and shorts.empty:
        return ConvictionTiltResult(
            targets=pd.DataFrame(),
            book_beta=BookBeta(0.0, 0.0, 0.0, 0, 0),
            gross=0.0,
            net=0.0,
            long_gross=0.0,
            short_gross=0.0,
            sector_net={},
        )

    side_gross = cfg.gross_target / 2.0  # gross is gross long + gross short
    long_base = side_gross / max(1, cfg.num_longs)
    short_base = -side_gross / max(1, cfg.num_shorts)

    longs = _apply_conviction_tilt(longs, base_weight=long_base)
    shorts = _apply_conviction_tilt(shorts, base_weight=short_base)

    longs = _apply_adv_cap(
        longs,
        max_position_pct=cfg.max_position_pct,
        adv_cap_pct=cfg.adv_cap_pct,
        target_aum_usd=target_aum_usd,
    )
    shorts = _apply_adv_cap(
        shorts,
        max_position_pct=cfg.max_position_pct,
        adv_cap_pct=cfg.adv_cap_pct,
        target_aum_usd=target_aum_usd,
    )

    longs = _apply_earnings_halve(longs)
    shorts = _apply_earnings_halve(shorts)

    longs, shorts, book_beta = _apply_beta_adjust(longs, shorts, betas=betas, max_beta=cfg.max_beta)

    longs, shorts, sector_net = _apply_sector_cap(longs, shorts, max_sector_pct=cfg.max_sector_pct)

    longs["final_weight"] = longs["beta_adjusted_weight"]
    shorts["final_weight"] = shorts["beta_adjusted_weight"]
    longs["side"] = "long"
    shorts["side"] = "short"

    targets = pd.concat([longs, shorts], ignore_index=True)
    targets["target_dollar"] = targets["final_weight"] * target_aum_usd
    targets["final_shares"] = np.where(
        targets["price"].fillna(0) > 0,
        np.round(targets["target_dollar"] / targets["price"]),
        0.0,
    )
    targets["limit_price"] = targets["price"]
    targets["base_weight_abs"] = targets["base_weight"].abs()

    long_gross = float(longs["final_weight"].sum())
    short_gross = float(shorts["final_weight"].abs().sum())
    gross = long_gross + short_gross
    net = long_gross - short_gross

    # Recompute book beta with the post-sector-cap weights for accurate report.
    final_weights = targets.set_index("ticker")["final_weight"]
    book_beta = aggregate_book_beta(weights=final_weights, betas=betas)

    return ConvictionTiltResult(
        targets=targets,
        book_beta=book_beta,
        gross=gross,
        net=net,
        long_gross=long_gross,
        short_gross=short_gross,
        sector_net=sector_net,
    )


# -----------------------------------------------------------------------------
# Candidate-DataFrame loader (DB → DataFrame for the CLI)
# -----------------------------------------------------------------------------


def load_candidate_frame(
    conn: sqlite3.Connection,
    *,
    asof: date_type,
    earnings_window_days: int,
    adv_lookback: int = 20,
) -> pd.DataFrame:
    """Build a candidate DataFrame from L1 + L2 tables for ``asof``.

    Columns produced:
      ticker, sector, score, price, adv_usd, has_earnings_within_window,
      earnings_date.

    ``score`` is the combined-factor percentile_rank in ``factor_scores``.
    ``price`` is the latest adj_close on or before ``asof``.
    ``adv_usd`` is mean(adj_close * volume) over the trailing ``adv_lookback``
    trading days ending at ``asof``.
    """
    scores = pd.read_sql_query(
        """
        SELECT fs.ticker, u.sector, fs.percentile_rank AS score
        FROM factor_scores fs
        JOIN universe u ON u.ticker = fs.ticker
        WHERE fs.score_date = ?
          AND fs.factor = 'combined'
          AND fs.sub_factor = 'combined'
          AND u.delisted_date IS NULL
        """,
        conn,
        params=[asof.isoformat()],
    )
    if scores.empty:
        return scores.assign(
            price=pd.Series(dtype=float),
            adv_usd=pd.Series(dtype=float),
            has_earnings_within_window=pd.Series(dtype=bool),
            earnings_date=pd.Series(dtype=str),
        )

    # Most recent price on or before asof + adv_usd over trailing window
    horizon = (pd.Timestamp(asof) - pd.Timedelta(days=adv_lookback * 2 + 5)).date().isoformat()
    px = pd.read_sql_query(
        """
        SELECT ticker, date, adj_close, volume
        FROM daily_prices
        WHERE ticker IN ({}) AND date <= ? AND date >= ?
        ORDER BY ticker, date
        """.format(",".join("?" * len(scores))),
        conn,
        params=[*scores["ticker"].tolist(), asof.isoformat(), horizon],
    )
    px["adj_close"] = pd.to_numeric(px["adj_close"], errors="coerce")
    px["volume"] = pd.to_numeric(px["volume"], errors="coerce").fillna(0.0)
    px["dollar_vol"] = px["adj_close"] * px["volume"]

    last_px = px.dropna(subset=["adj_close"]).groupby("ticker").tail(1)
    last_px = last_px[["ticker", "adj_close"]].rename(columns={"adj_close": "price"})

    adv = (
        px.groupby("ticker")
        .tail(adv_lookback)
        .groupby("ticker")["dollar_vol"]
        .mean()
        .reset_index()
        .rename(columns={"dollar_vol": "adv_usd"})
    )

    out = scores.merge(last_px, on="ticker", how="left").merge(adv, on="ticker", how="left")

    # Earnings window
    earn_lo = asof.isoformat()
    earn_hi = (asof + timedelta(days=earnings_window_days)).isoformat()
    earn = pd.read_sql_query(
        """
        SELECT ticker, MIN(expected_date) AS earnings_date
        FROM earnings_calendar
        WHERE expected_date BETWEEN ? AND ?
          AND ticker IN ({})
        GROUP BY ticker
        """.format(",".join("?" * len(scores))),
        conn,
        params=[earn_lo, earn_hi, *scores["ticker"].tolist()],
    )
    out = out.merge(earn, on="ticker", how="left")
    out["has_earnings_within_window"] = out["earnings_date"].notna()
    return out


def load_candidate_betas(
    conn: sqlite3.Connection,
    *,
    candidates: pd.DataFrame,
    asof: date_type,
    lookback: int = DEFAULT_BETA_LOOKBACK,
) -> dict[str, float]:
    """Convenience wrapper that mirrors :func:`compute_betas`."""
    return compute_betas(conn, tickers=candidates["ticker"].tolist(), asof=asof, lookback=lookback)


__all__ = [
    "TILT_BASE_MULT",
    "TILT_TOP5_MULT",
    "TILT_TOP10_MULT",
    "ConvictionTiltOptimizer",
    "ConvictionTiltResult",
    "build_target_book",
    "load_candidate_betas",
    "load_candidate_frame",
    "select_candidates",
]
