"""FIFO position attribution and signal-quality metrics (REPORT-02)."""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass
from typing import cast

import pandas as pd

BUCKETS = ("1-5d", "5-20d", "20-60d", "60d+")


@dataclass(frozen=True)
class RoundTrip:
    ticker: str
    side: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: float
    entry_score: float | None
    realized_pnl: float
    holding_days: int
    holding_bucket: str
    sector: str | None = None
    vix_at_entry: float | None = None
    factor_quintile_at_entry: int | None = None

    @property
    def realized_return(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        raw = (self.exit_price - self.entry_price) / self.entry_price
        return raw if self.side == "long" else -raw


def holding_bucket(days: int) -> str:
    if days <= 5:
        return "1-5d"
    if days <= 20:
        return "5-20d"
    if days <= 60:
        return "20-60d"
    return "60d+"


def fifo_round_trips(trades: pd.DataFrame) -> list[RoundTrip]:
    lots: dict[tuple[str, str], deque[pd.Series]] = {}
    trips: list[RoundTrip] = []
    for _, row in trades.sort_values("date").iterrows():
        ticker = str(row["ticker"])
        qty = float(row["shares"])
        if qty == 0:
            continue
        key = (ticker, "long" if qty > 0 else "short")
        close_key = (ticker, "short" if qty > 0 else "long")
        remaining = abs(qty)
        price = float(row["price"])
        date = pd.Timestamp(row["date"])
        while remaining > 0 and close_key in lots and lots[close_key]:
            lot = lots[close_key][0]
            lot_qty = float(lot["remaining"])
            matched = min(remaining, lot_qty)
            entry_price = float(lot["price"])
            entry_date = pd.Timestamp(lot["date"])
            lot_side = str(lot["side"])
            pnl = (price - entry_price) * matched if lot_side == "long" else (entry_price - price) * matched
            days = max(1, int((date - entry_date).days))
            trips.append(
                RoundTrip(
                    ticker=ticker,
                    side=lot_side,
                    entry_date=entry_date.date().isoformat(),
                    exit_date=date.date().isoformat(),
                    entry_price=entry_price,
                    exit_price=price,
                    shares=matched,
                    entry_score=_nullable_float(lot.get("entry_score")),
                    realized_pnl=pnl,
                    holding_days=days,
                    holding_bucket=holding_bucket(days),
                    sector=lot.get("sector"),
                    vix_at_entry=_nullable_float(lot.get("vix_at_entry")),
                    factor_quintile_at_entry=_nullable_int(lot.get("factor_quintile_at_entry")),
                )
            )
            lot["remaining"] = lot_qty - matched
            remaining -= matched
            if float(lot["remaining"]) <= 0:
                lots[close_key].popleft()
        if remaining > 0:
            lots.setdefault(key, deque()).append(
                pd.Series(
                    {
                        **row.to_dict(),
                        "remaining": remaining,
                        "side": "long" if qty > 0 else "short",
                    }
                )
            )
    return trips


def round_trips_frame(trips: list[RoundTrip]) -> pd.DataFrame:
    return pd.DataFrame([trip.__dict__ | {"realized_return": trip.realized_return} for trip in trips])


def spearman_signal_quality(trips: list[RoundTrip]) -> float:
    df = round_trips_frame(trips)
    if df.empty or df["entry_score"].nunique(dropna=True) < 2:
        return 0.0
    corr = df["entry_score"].corr(df["realized_return"], method="spearman")
    return float(corr) if pd.notna(corr) else 0.0


def best_worst_by_side(trips: list[RoundTrip], *, n: int = 5) -> dict[str, pd.DataFrame]:
    df = round_trips_frame(trips)
    out: dict[str, pd.DataFrame] = {}
    for side in ("long", "short"):
        sub = df[df["side"] == side].sort_values("realized_pnl", ascending=False)
        out[f"{side}_best"] = sub.head(n)
        out[f"{side}_worst"] = sub.tail(n).sort_values("realized_pnl")
    return out


def persist_position_attribution(conn: sqlite3.Connection, trips: list[RoundTrip]) -> None:
    with conn:
        conn.executemany(
            """
            INSERT INTO position_attribution (
                ticker, side, entry_date, exit_date, entry_price, exit_price,
                entry_score, realized_pnl, holding_days, holding_bucket,
                sector, vix_at_entry, factor_quintile_at_entry
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    t.ticker,
                    t.side,
                    t.entry_date,
                    t.exit_date,
                    t.entry_price,
                    t.exit_price,
                    t.entry_score,
                    t.realized_pnl,
                    t.holding_days,
                    t.holding_bucket,
                    t.sector,
                    t.vix_at_entry,
                    t.factor_quintile_at_entry,
                )
                for t in trips
            ],
        )


def _nullable_float(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(cast(float, value))


def _nullable_int(value: object) -> int | None:
    return None if value is None or pd.isna(value) else int(cast(int, value))


__all__ = [
    "RoundTrip",
    "best_worst_by_side",
    "fifo_round_trips",
    "holding_bucket",
    "persist_position_attribution",
    "round_trips_frame",
    "spearman_signal_quality",
]
