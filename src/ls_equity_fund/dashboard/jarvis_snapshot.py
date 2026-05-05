"""Build and cache the bounded JARVIS system-state snapshot (DASH-09)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_SNAPSHOT_BYTES = 19_000


def build_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "portfolio": _portfolio(conn),
        "risk": _risk(conn),
        "performance": _performance(conn),
        "execution": _execution(conn),
        "scoring": _scoring(conn),
        "reporting": _reporting(conn),
    }


def write_snapshot(conn: sqlite3.Connection, path: Path) -> Path:
    snapshot = build_snapshot(conn)
    text = _bounded_json(snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bounded_json(snapshot: dict[str, Any]) -> str:
    text = json.dumps(snapshot, sort_keys=True, default=str)
    if len(text.encode("utf-8")) <= MAX_SNAPSHOT_BYTES:
        return text
    compact = snapshot.copy()
    for section in compact.values():
        if isinstance(section, dict):
            for key, value in list(section.items()):
                if isinstance(value, list):
                    section[key] = value[:5]
    text = json.dumps(compact, sort_keys=True, default=str)
    return text[:MAX_SNAPSHOT_BYTES]


def _read_df(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []


def _portfolio(conn: sqlite3.Connection) -> dict[str, Any]:
    positions = _read_df(conn, "SELECT ticker, side, shares, current_price, sector FROM portfolio_positions LIMIT 30")
    agg = _read_df(conn, "SELECT gross_exposure, net_beta FROM portfolio_history WHERE ticker='__PORTFOLIO__' ORDER BY asof_date DESC LIMIT 1")
    sector_weights: dict[str, float] = {}
    for p in positions:
        sector = str(p.get("sector") or "Unknown")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + abs(float(p.get("shares") or 0.0))
    return {"positions": positions[:20], "gross_exposure": (agg[0].get("gross_exposure") if agg else 0), "net_beta": (agg[0].get("net_beta") if agg else 0), "sector_weights": sector_weights}


def _risk(conn: sqlite3.Connection) -> dict[str, Any]:
    mctr = _read_df(conn, "SELECT ticker, mctr FROM risk_snapshots ORDER BY timestamp DESC LIMIT 5")
    breakers = _read_df(conn, "SELECT breaker_type, observed_value, threshold FROM circuit_breaker_log ORDER BY timestamp DESC LIMIT 5")
    return {"circuit_breaker_status": breakers, "VaR_95": 0.0, "CVaR_95": 0.0, "max_dd_current": 0.0, "mctr_top5": mctr, "factor_exposures": []}


def _performance(conn: sqlite3.Connection) -> dict[str, Any]:
    metrics = _read_df(conn, "SELECT metric_name, metric_value FROM tear_sheet_metrics ORDER BY date DESC LIMIT 20")
    curve = _read_df(conn, "SELECT date, daily_return FROM daily_attribution ORDER BY date DESC LIMIT 30")
    return {"metrics": metrics, "equity_curve_last_30d": curve}


def _execution(conn: sqlite3.Connection) -> dict[str, Any]:
    orders_today = _read_df(conn, "SELECT ticker, side, status, slippage_bps FROM orders ORDER BY timestamp DESC LIMIT 30")
    htb = _read_df(conn, "SELECT ticker, rate_pct FROM borrow_rates WHERE is_htb=1 ORDER BY as_of_date DESC LIMIT 10")
    pending = [o for o in orders_today if str(o.get("status", "")).lower() in {"submitted", "partial"}]
    slips = [float(o.get("slippage_bps") or 0.0) for o in orders_today]
    return {"orders_today": len(orders_today), "pending_count": len(pending), "avg_slippage_30d": sum(slips) / len(slips) if slips else 0.0, "htb_names": htb}


def _scoring(conn: sqlite3.Connection) -> dict[str, Any]:
    longs = _read_df(conn, "SELECT ticker, parent_score FROM factor_scores_parent WHERE factor='combined' ORDER BY score_date DESC, parent_score DESC LIMIT 10")
    shorts = _read_df(conn, "SELECT ticker, parent_score FROM factor_scores_parent WHERE factor='combined' ORDER BY score_date DESC, parent_score ASC LIMIT 10")
    return {"top_10_longs": longs, "top_10_shorts": shorts, "vix_regime": "UNKNOWN"}


def _reporting(conn: sqlite3.Connection) -> dict[str, Any]:
    attr = _read_df(conn, "SELECT * FROM daily_attribution ORDER BY date DESC LIMIT 1")
    commentary = _read_df(conn, "SELECT body_md FROM weekly_commentary ORDER BY week_ending DESC LIMIT 1")
    snippet = str(commentary[0].get("body_md", ""))[:500] if commentary else ""
    return {"daily_pnl": attr[0].get("daily_return") if attr else 0.0, "attribution_today": attr[0] if attr else {}, "week_commentary_snippet": snippet}


__all__ = ["MAX_SNAPSHOT_BYTES", "build_snapshot", "write_snapshot"]
