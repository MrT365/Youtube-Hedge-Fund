"""Win/loss, streak, VIX-regime, and sector-alpha analytics (REPORT-03/04)."""

from __future__ import annotations

import pandas as pd


def vix_regime(vix: float) -> str:
    if vix < 15:
        return "CALM"
    if vix < 25:
        return "NORMAL"
    if vix < 35:
        return "ELEVATED"
    return "CRISIS"


def win_loss_slices(trips: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if trips.empty:
        empty = pd.DataFrame(columns=["bucket", "trades", "wins", "losses"])
        return {"side": empty, "holding_bucket": empty, "sector": empty, "vix_regime": empty, "factor_quintile": empty}
    df = trips.copy()
    df["won"] = df["realized_pnl"] > 0
    df["vix_regime"] = df["vix_at_entry"].fillna(0.0).map(vix_regime)
    out: dict[str, pd.DataFrame] = {}
    for col, name in [
        ("side", "side"),
        ("holding_bucket", "holding_bucket"),
        ("sector", "sector"),
        ("vix_regime", "vix_regime"),
        ("factor_quintile_at_entry", "factor_quintile"),
    ]:
        grouped = df.groupby(col, dropna=False)["won"].agg(["count", "sum"])
        out[name] = grouped.rename(columns={"count": "trades", "sum": "wins"}).assign(
            losses=lambda x: x["trades"] - x["wins"]
        ).reset_index(names="bucket")
    return out


def streaks(trips: pd.DataFrame) -> tuple[int, int]:
    wins = (trips.sort_values("exit_date")["realized_pnl"] > 0).tolist()
    longest_win = longest_loss = current_win = current_loss = 0
    for won in wins:
        if won:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)
    return longest_win, longest_loss


def sector_relative_alpha(picks: pd.DataFrame) -> pd.DataFrame:
    if picks.empty:
        return pd.DataFrame(columns=["sector", "pick_return", "sector_etf_return", "selection_alpha"])
    out = picks.copy()
    out["selection_alpha"] = out["pick_return"] - out["sector_etf_return"]
    return out.groupby("sector", as_index=False).agg(
        pick_return=("pick_return", "sum"),
        sector_etf_return=("sector_etf_return", "sum"),
        selection_alpha=("selection_alpha", "sum"),
    )


def sector_alpha_summary(alpha: pd.DataFrame) -> dict[str, float | int]:
    if alpha.empty:
        return {"total_alpha": 0.0, "winner_sectors": 0, "loser_sectors": 0}
    return {
        "total_alpha": float(alpha["selection_alpha"].sum()),
        "winner_sectors": int((alpha["selection_alpha"] > 0).sum()),
        "loser_sectors": int((alpha["selection_alpha"] <= 0).sum()),
    }


__all__ = ["sector_alpha_summary", "sector_relative_alpha", "streaks", "vix_regime", "win_loss_slices"]
