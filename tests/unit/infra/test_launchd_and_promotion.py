from __future__ import annotations

import importlib.util
import os
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ls_equity_fund.config import BrokerConfig
from ls_equity_fund.execution.broker import IBKRBroker, LiveTradingGateError

PROMOTE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "promote_to_live.py"
spec = importlib.util.spec_from_file_location("promote_to_live", PROMOTE_PATH)
promote_mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["promote_to_live"] = promote_mod
spec.loader.exec_module(promote_mod)


def test_plist_required_fields() -> None:
    path = Path("scripts/com.user.hedgefund.daily.plist")
    data = plistlib.loads(path.read_bytes())
    assert data["WakeSystem"] is True
    assert "StandardOutPath" in data
    assert "StandardErrorPath" in data
    assert data["Label"] == "com.user.hedgefund.daily"


def test_run_daily_writes_heartbeat_with_mock_meridian(tmp_path: Path) -> None:
    root = Path.cwd()
    mockbin = tmp_path / "bin"
    mockbin.mkdir()
    meridian = mockbin / "meridian"
    meridian.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    meridian.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{mockbin}:{env['PATH']}"
    result = subprocess.run(["bash", str(root / "scripts/run_daily.sh")], cwd=root, env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 0
    text = (root / "cache/last_run_completed.txt").read_text(encoding="utf-8")
    assert "completed_at=" in text and "exit_code=0" in text


def _promotion_db(path: Path, *, fail_one: bool = False) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE daily_attribution (date TEXT);
        CREATE TABLE tear_sheet_metrics (metric_name TEXT, metric_value REAL);
        CREATE TABLE orders (timestamp INTEGER, slippage_bps REAL);
        CREATE TABLE veto_log (reason TEXT);
        CREATE TABLE circuit_breaker_log (breaker_type TEXT, timestamp INTEGER);
        """
    )
    for i in range(40 if not fail_one else 39):
        conn.execute("INSERT INTO daily_attribution VALUES (?)", (f"2026-01-{i + 1:02d}",))
    conn.execute("INSERT INTO tear_sheet_metrics VALUES ('max_drawdown', -0.10)")
    for i in range(4):
        conn.execute("INSERT INTO tear_sheet_metrics VALUES (?, 0.04)", (f"factor_ic_{i}",))
    conn.execute("INSERT INTO orders VALUES (strftime('%s','now'), 10.0)")
    conn.commit()
    conn.close()
    return path


def test_promotion_all_pass_writes_record(tmp_path: Path) -> None:
    db = _promotion_db(tmp_path / "ok.db")
    out = tmp_path / "promotion_record.json"
    record = promote_mod.promote(account_number="DU1234567", output_path=out, db_path=db, input_text="DU1234567")
    assert out.exists()
    assert record["operator_confirmed"] is True
    assert record["account_number_last4"] == "4567"


def test_promotion_any_fail_hard_exits_no_record(tmp_path: Path) -> None:
    db = _promotion_db(tmp_path / "bad.db", fail_one=True)
    out = tmp_path / "promotion_record.json"
    with pytest.raises(SystemExit):
        promote_mod.promote(account_number="DU1234567", output_path=out, db_path=db, input_text="DU1234567")
    assert not out.exists()


def test_ibkr_live_gate_requires_promotion_record_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = tmp_path / "promotion_record.json"
    cfg = BrokerConfig(mode="live", audit_promotion_path=str(record))
    monkeypatch.delenv("MERIDIAN_LIVE_OK", raising=False)
    with pytest.raises(LiveTradingGateError):
        IBKRBroker(cfg, connect=False)
    monkeypatch.setenv("MERIDIAN_LIVE_OK", "1")
    with pytest.raises(LiveTradingGateError):
        IBKRBroker(cfg, connect=False)
    record.write_text("{}", encoding="utf-8")
    assert IBKRBroker(cfg, connect=False).is_paper is False
