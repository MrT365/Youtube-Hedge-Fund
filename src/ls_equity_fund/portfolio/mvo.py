"""SLSQP mean-variance optimizer (PORT-02 / PORT-03).

MVO consumes Phase 6's Ledoit-Wolf-shrunk predicted covariance matrix. It never
uses raw sample covariance internally. Failures fall back to conviction-tilt and
write an immutable audit row.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ls_equity_fund.config import PortfolioConfig
from ls_equity_fund.portfolio.base import Optimizer
from ls_equity_fund.portfolio.beta import BookBeta, aggregate_book_beta
from ls_equity_fund.portfolio.conviction_tilt import (
    ConvictionTiltResult,
    build_target_book,
    select_candidates,
)
from ls_equity_fund.portfolio.transaction_cost import estimate_trade_cost

EXPECTED_RETURN_MIN = -0.15
EXPECTED_RETURN_MAX = 0.15
MIN_ACCEPTED_ANNUAL_VOL = 0.05
FALLBACK_USED = "conviction_tilt"
CONSTRAINT_TOL = 1e-8


class MVOFailure(RuntimeError):
    """Raised when MVO cannot produce an acceptable target book."""


@dataclass(frozen=True)
class MVOResult:
    targets: pd.DataFrame
    book_beta: BookBeta
    gross: float
    net: float
    long_gross: float
    short_gross: float
    sector_net: dict[str, float]
    annualized_vol: float
    used_fallback: bool = False
    fallback_reason: str | None = None
    flags: list[str] = field(default_factory=list)


class MVOOptimizer(Optimizer):
    """SLSQP optimizer behind the Optimizer ABC seam."""

    def __init__(self, cfg: PortfolioConfig) -> None:
        self._cfg = cfg

    def optimize(
        self,
        candidates: pd.DataFrame,
        cov: Any | None,
        constraints: Any,
    ) -> pd.DataFrame:
        result = build_mvo_target_book(
            candidates,
            cfg=self._cfg,
            covariance=_coerce_covariance(cov),
            betas=(constraints or {}).get("betas", {}) if isinstance(constraints, dict) else {},
            target_aum_usd=(constraints or {}).get("target_aum_usd") if isinstance(constraints, dict) else None,
        )
        return result.targets


def build_mvo_target_book(
    candidates: pd.DataFrame,
    *,
    cfg: PortfolioConfig,
    covariance: pd.DataFrame,
    betas: dict[str, float] | None = None,
    target_aum_usd: float | None = None,
) -> MVOResult:
    """Produce a 20L/20S MVO target book satisfying Phase 7 constraints."""
    if covariance.empty:
        raise MVOFailure("missing_ledoit_wolf_covariance")
    target_aum_usd = target_aum_usd or cfg.target_aum_usd
    betas = betas or {}
    longs, shorts = select_candidates(candidates, cfg=cfg)
    if len(longs) < cfg.num_longs or len(shorts) < cfg.num_shorts:
        raise MVOFailure("insufficient_candidates_for_20_long_20_short")
    longs = longs.head(cfg.num_longs).copy()
    shorts = shorts.head(cfg.num_shorts).copy()
    longs["side"] = "long"
    shorts["side"] = "short"
    selected = pd.concat([longs, shorts], ignore_index=True)
    tickers = selected["ticker"].tolist()
    covariance = covariance.reindex(index=tickers, columns=tickers).fillna(0.0)
    if covariance.shape != (len(tickers), len(tickers)):
        raise MVOFailure("covariance_shape_mismatch")

    mu = _expected_returns_net_of_cost(selected, cfg=cfg, target_aum_usd=target_aum_usd)
    cov_arr = covariance.to_numpy(dtype=float)
    long_idx = np.array(selected["side"] == "long")
    short_idx = np.array(selected["side"] == "short")
    max_pos = cfg.max_position_pct
    side_gross = cfg.gross_target / 2.0
    bounds = [(0.0, max_pos) if is_long else (-max_pos, 0.0) for is_long in long_idx]
    x0 = np.where(long_idx, side_gross / cfg.num_longs, -side_gross / cfg.num_shorts)
    beta_vec = np.array([betas.get(t, 0.0) for t in tickers], dtype=float)

    constraints = [
        {"type": "eq", "fun": lambda w: np.sum(w[long_idx]) - side_gross},
        {"type": "eq", "fun": lambda w: -np.sum(w[short_idx]) - side_gross},
        {"type": "ineq", "fun": lambda w: cfg.net_target_high - np.sum(w) + CONSTRAINT_TOL},
        {"type": "ineq", "fun": lambda w: np.sum(w) - cfg.net_target_low + CONSTRAINT_TOL},
        {"type": "ineq", "fun": lambda w: 0.20 - abs(float(np.dot(w, beta_vec))) + CONSTRAINT_TOL},
    ]
    constraints.extend(_sector_constraints(selected, cfg))

    def objective(w: np.ndarray) -> float:
        return -float(np.dot(mu, w) - 0.5 * cfg.mvo_risk_aversion * (w @ cov_arr @ w))

    result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-9, "disp": False},
    )
    if not result.success:
        raise MVOFailure(f"mvo_non_convergence:{result.message}")

    weights = pd.Series(result.x, index=tickers, dtype=float)
    annualized_vol = model_implied_vol(weights, covariance)
    if annualized_vol < MIN_ACCEPTED_ANNUAL_VOL:
        raise MVOFailure(f"vol_sanity_check_failed:{annualized_vol:.6f}")

    targets = _targets_from_weights(selected, weights=weights, target_aum_usd=target_aum_usd)
    book_beta = aggregate_book_beta(weights=weights, betas=betas)
    long_gross = float(weights[weights > 0].sum())
    short_gross = float(weights[weights < 0].abs().sum())
    sector_net = _sector_net(targets)
    return MVOResult(
        targets=targets,
        book_beta=book_beta,
        gross=long_gross + short_gross,
        net=long_gross - short_gross,
        long_gross=long_gross,
        short_gross=short_gross,
        sector_net=sector_net,
        annualized_vol=annualized_vol,
    )


def build_mvo_or_fallback(
    conn: sqlite3.Connection,
    candidates: pd.DataFrame,
    *,
    cfg: PortfolioConfig,
    covariance: pd.DataFrame,
    betas: dict[str, float] | None = None,
    target_aum_usd: float | None = None,
) -> MVOResult:
    """Run MVO; on failure audit and return fresh conviction-tilt targets."""
    betas = betas or {}
    target_aum_usd = target_aum_usd or cfg.target_aum_usd
    try:
        return build_mvo_target_book(
            candidates,
            cfg=cfg,
            covariance=covariance,
            betas=betas,
            target_aum_usd=target_aum_usd,
        )
    except MVOFailure as exc:
        reason = str(exc)
        fallback = build_target_book(
            candidates,
            cfg=cfg,
            betas=betas,
            target_aum_usd=target_aum_usd,
        )
        write_optimizer_fallback_log(
            conn,
            reason=reason,
            portfolio_state={
                "n_candidates": len(candidates),
                "n_fallback_targets": len(fallback.targets),
                "fallback_gross": fallback.gross,
                "fallback_net": fallback.net,
            },
        )
        return _from_conviction_fallback(fallback, reason=reason)


def write_optimizer_fallback_log(
    conn: sqlite3.Connection,
    *,
    reason: str,
    portfolio_state: dict[str, Any],
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO optimizer_fallback_log (
                timestamp, reason, fallback_used, portfolio_state_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                int(time.time()),
                reason,
                FALLBACK_USED,
                json.dumps(portfolio_state, sort_keys=True, default=str),
            ),
        )


def model_implied_vol(weights: pd.Series, covariance: pd.DataFrame) -> float:
    cov = covariance.reindex(index=weights.index, columns=weights.index).fillna(0.0)
    variance = float(weights.to_numpy(dtype=float).T @ cov.to_numpy(dtype=float) @ weights.to_numpy(dtype=float))
    return float(np.sqrt(max(variance, 0.0)))


def _expected_returns_net_of_cost(
    selected: pd.DataFrame,
    *,
    cfg: PortfolioConfig,
    target_aum_usd: float,
) -> np.ndarray:
    score = pd.to_numeric(selected["score"], errors="coerce").fillna(50.0).to_numpy(dtype=float)
    expected = EXPECTED_RETURN_MIN + (score / 100.0) * (EXPECTED_RETURN_MAX - EXPECTED_RETURN_MIN)
    costs = []
    for _, row in selected.iterrows():
        price = float(row.get("price") or row.get("limit_price") or 0.0)
        adv_usd = float(row.get("adv_usd") or 0.0)
        shares = (cfg.max_position_pct * target_aum_usd / price) if price > 0 else 0.0
        cost = estimate_trade_cost(
            shares=shares,
            price=price,
            adv_usd=adv_usd,
            cfg=cfg.transaction_cost,
            is_sell=row.get("side") == "short",
        )
        costs.append(cost.total_bps / 10_000.0)
    return np.asarray(expected - np.array(costs, dtype=float), dtype=float)


def _sector_constraints(selected: pd.DataFrame, cfg: PortfolioConfig) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    sectors = sorted(selected["sector"].fillna("Unknown").unique())
    sides = selected["side"].to_numpy()
    for sector in sectors:
        sec_mask = (selected["sector"].fillna("Unknown") == sector).to_numpy()
        long_sec = sec_mask & (sides == "long")
        short_sec = sec_mask & (sides == "short")
        constraints.append(
            {"type": "ineq", "fun": lambda w, m=long_sec: cfg.max_sector_pct - np.sum(w[m]) + CONSTRAINT_TOL}
        )
        constraints.append(
            {"type": "ineq", "fun": lambda w, m=short_sec: cfg.max_sector_pct + np.sum(w[m]) + CONSTRAINT_TOL}
        )
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda w, lm=long_sec, sm=short_sec: cfg.max_sector_pct
                - abs(float(np.sum(w[lm]) + np.sum(w[sm])))
                + CONSTRAINT_TOL,
            }
        )
    return constraints


def _targets_from_weights(
    selected: pd.DataFrame,
    *,
    weights: pd.Series,
    target_aum_usd: float,
) -> pd.DataFrame:
    targets = selected.copy()
    targets["final_weight"] = targets["ticker"].map(weights).astype(float)
    targets["base_weight"] = np.where(targets["side"] == "long", targets["final_weight"].abs(), -targets["final_weight"].abs())
    targets["tilted_weight"] = targets["final_weight"]
    targets["adv_capped_weight"] = targets["final_weight"]
    targets["earnings_halved"] = False
    targets["beta_adjusted_weight"] = targets["final_weight"]
    targets["tilt_bucket"] = "mvo"
    targets["target_dollar"] = targets["final_weight"] * target_aum_usd
    targets["limit_price"] = targets["price"]
    targets["final_shares"] = np.where(
        targets["price"].fillna(0) > 0,
        np.round(targets["target_dollar"] / targets["price"]),
        0.0,
    )
    return targets


def _sector_net(targets: pd.DataFrame) -> dict[str, float]:
    long_w = targets[targets["side"] == "long"].groupby("sector")["final_weight"].sum()
    short_w = targets[targets["side"] == "short"].groupby("sector")["final_weight"].apply(lambda s: s.abs().sum())
    all_sectors = sorted(set(long_w.index).union(short_w.index))
    return {sec: float(long_w.get(sec, 0.0) - short_w.get(sec, 0.0)) for sec in all_sectors}


def _from_conviction_fallback(result: ConvictionTiltResult, *, reason: str) -> MVOResult:
    return MVOResult(
        targets=result.targets,
        book_beta=result.book_beta,
        gross=result.gross,
        net=result.net,
        long_gross=result.long_gross,
        short_gross=result.short_gross,
        sector_net=result.sector_net,
        annualized_vol=float("nan"),
        used_fallback=True,
        fallback_reason=reason,
        flags=["fallback_conviction_tilt"],
    )


def _coerce_covariance(cov: Any | None) -> pd.DataFrame:
    predicted = getattr(cov, "predicted_covariance", None)
    if isinstance(predicted, pd.DataFrame):
        return predicted
    if isinstance(cov, pd.DataFrame):
        return cov
    return pd.DataFrame()


__all__ = [
    "CONSTRAINT_TOL",
    "EXPECTED_RETURN_MAX",
    "EXPECTED_RETURN_MIN",
    "FALLBACK_USED",
    "MIN_ACCEPTED_ANNUAL_VOL",
    "MVOFailure",
    "MVOOptimizer",
    "MVOResult",
    "build_mvo_or_fallback",
    "build_mvo_target_book",
    "model_implied_vol",
    "write_optimizer_fallback_log",
]
