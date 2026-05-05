#!/usr/bin/env python3
"""AUDIT-03 paper-to-live promotion ceremony."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "cache" / "ls_equity_fund.db"
OUTPUT = REPO_ROOT / "output" / "promotion_record.json"


@dataclass(frozen=True)
class Criterion:
    name: str
    actual: float
    threshold: float
    passed: bool


def evaluate_criteria(db_path: Path = DB_PATH) -> list[Criterion]:
    conn = sqlite3.connect(str(db_path)) if db_path.exists() else sqlite3.connect(":memory:")
    try:
        trading_days = _scalar(conn, "SELECT COUNT(DISTINCT date) FROM daily_attribution")
        max_dd = abs(_scalar(conn, "SELECT COALESCE(MIN(metric_value), 0) FROM tear_sheet_metrics WHERE metric_name='max_drawdown'"))
        avg_slip = abs(_scalar(conn, "SELECT COALESCE(AVG(slippage_bps), 0) FROM orders WHERE timestamp >= strftime('%s','now','-30 days')"))
        factor_ic_pass = _scalar(conn, "SELECT COUNT(*) FROM tear_sheet_metrics WHERE metric_name LIKE 'factor_ic_%' AND metric_value > 0.03")
        veto_bypass = _scalar(conn, "SELECT COUNT(*) FROM veto_log WHERE reason LIKE '%bypass%'")
        stale_halts = _scalar(conn, "SELECT COUNT(*) FROM circuit_breaker_log WHERE breaker_type='STALE_CACHE' AND timestamp >= strftime('%s','now','-30 days')")
    finally:
        conn.close()
    return [
        Criterion("paper_trading_days", trading_days, 40, trading_days >= 40),
        Criterion("max_drawdown_abs", max_dd, 0.15, max_dd < 0.15),
        Criterion("avg_slippage_bps_abs", avg_slip, 50, avg_slip <= 50),
        Criterion("factor_ic_count_gt_0.03", factor_ic_pass, 4, factor_ic_pass >= 4),
        Criterion("veto_bypass_events", veto_bypass, 0, veto_bypass == 0),
        Criterion("stale_cache_halts_30d", stale_halts, 0, stale_halts == 0),
    ]


def promote(*, account_number: str, output_path: Path = OUTPUT, db_path: Path = DB_PATH, input_text: str | None = None) -> dict[str, object]:
    criteria = evaluate_criteria(db_path)
    for c in criteria:
        print(f"{'PASS' if c.passed else 'FAIL'} {c.name}: actual={c.actual} threshold={c.threshold}")
    if not all(c.passed for c in criteria):
        raise SystemExit(2)
    print("\nALL CRITERIA MET — PROMOTION ELIGIBLE\n")
    typed = input_text if input_text is not None else input("Type live account number to proceed: ")
    if typed != account_number:
        raise SystemExit(3)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "criteria_values": [asdict(c) for c in criteria],
        "account_number_last4": account_number[-4:],
        "operator_confirmed": True,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    print("Promotion record written. Live mode now unlockable.")
    return record


def _scalar(conn: sqlite3.Connection, sql: str) -> float:
    try:
        row = conn.execute(sql).fetchone()
        return float(row[0] or 0) if row else 0.0
    except sqlite3.OperationalError:
        return 0.0


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-number", required=True)
    args = parser.parse_args()
    promote(account_number=args.account_number)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Promotion failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
